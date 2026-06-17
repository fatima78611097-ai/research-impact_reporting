"""Launch a g6.2xlarge spot, run the Qwen2.5-VL-7B pairing eval, tear down.
Mirrors parse_documents._launch_instance exactly (same SSM AMI, IAM profile, SG,
us-east-1a subnet, spot) and deploys/runs via S3 + SSM AWS-RunShellScript, same as
the Docling worker. ALWAYS terminates the instance at the end.

    python3 launch_vl2.py                          # vl2-tiny (default)
    python3 launch_vl2.py deepseek-ai/deepseek-vl2-small
"""
import os, sys, time, boto3

REGION = "us-east-1"
INSTANCE_TYPE = os.environ.get("QWEN_INSTANCE", "g6.2xlarge")
SPOT = os.environ.get("QWEN_SPOT", "1") != "0"   # VL2_SPOT=0 -> on-demand (no spot reclaim)
SSM_AMI_PARAM = "/cloud2.lavandulagroup.com/docling-ami-id"
SUBNET_AZ = [                                # rotate on capacity failure
    ("subnet-0e2008e48d602e945", "us-east-1a"),
    ("subnet-0f77191c6900e912d", "us-east-1b"),
    ("subnet-0f80a930457d062d5", "us-east-1d"),
    ("subnet-0a92608217981d266", "us-east-1c"),
    ("subnet-0f2ce8c36edfe58f5", "us-east-1f"),
]
SECURITY_GROUP_ID = "sg-0d9a6217a104cfe35"
IAM_PROFILE_NAME = "cloud2_lavandulagroup"
BUCKET = "lavandula-nonprofit-collaterals"
TARBALL = "qwen_eval.tgz"
S3_TARBALL_KEY = f"deploy/{TARBALL}"
S3_LOG_KEY = "logs/qwen/qwen_out.log"
MODEL = ""
SCRIPT = ""

ec2 = boto3.client("ec2", region_name=REGION)
ssm = boto3.client("ssm", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)


def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


# 1. upload tarball
log(f"uploading {TARBALL} -> s3://{BUCKET}/{S3_TARBALL_KEY}")
s3.upload_file(TARBALL, BUCKET, S3_TARBALL_KEY)

# 2. AMI
ami = ssm.get_parameter(Name=SSM_AMI_PARAM)["Parameter"]["Value"]
log(f"AMI {ami}")

instance_id = None
try:
    # 3. launch g6.2xlarge spot, rotating AZs on capacity failure (price drifts;
    #    the real constraint is which AZ has spot capacity right now).
    for sub, az in SUBNET_AZ:
        try:
            r = ec2.run_instances(
                ImageId=ami, InstanceType=INSTANCE_TYPE, MinCount=1, MaxCount=1,
                IamInstanceProfile={"Name": IAM_PROFILE_NAME},
                SubnetId=sub, SecurityGroupIds=[SECURITY_GROUP_ID],
                **({"InstanceMarketOptions": {"MarketType": "spot"}} if SPOT else {}),
                BlockDeviceMappings=[{"DeviceName": "/dev/sda1", "Ebs": {
                    "VolumeSize": 150, "VolumeType": "gp3", "Throughput": 750, "Iops": 6000,
                    "DeleteOnTermination": True}}],
                TagSpecifications=[{"ResourceType": "instance", "Tags": [
                    {"Key": "Name", "Value": "qwen-pairing-eval"},
                    {"Key": "Project", "Value": "lavandula"},
                    {"Key": "Purpose", "Value": "qwen-pairing-eval"}]}])
            instance_id = r["Instances"][0]["InstanceId"]
            log(f"launched {instance_id} ({INSTANCE_TYPE} {'spot' if SPOT else 'on-demand'}, {az})")
            break
        except ec2.exceptions.ClientError as e:
            code = e.response["Error"]["Code"]
            if "Capacity" in code or code in ("SpotMaxPriceTooLow", "Unsupported"):
                log(f"  {az}: {code} — trying next AZ")
                continue
            raise
    if instance_id is None:
        raise RuntimeError("no configured AZ had g6.2xlarge spot capacity")

    # 4. wait running
    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    log("running; waiting for SSM registration ...")
    for _ in range(60):
        info = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}])
        if info["InstanceInformationList"]:
            break
        time.sleep(5)
    else:
        raise RuntimeError("instance never registered with SSM")
    log("SSM ready; sending run command")
    log_key = f"logs/qwen/{instance_id}.log"     # per-run key: never read a stale log

    # 5. deploy + run via SSM. Stream the log to S3 every 30s so a timed-out/killed
    #    run STILL leaves partial progress — no more flying blind on the end-only upload.
    script = f"""
W=/opt/qwenwork
mkdir -p $W/tmp $W/pip $W/hf
export TMPDIR=$W/tmp PIP_CACHE_DIR=$W/pip HF_HOME=$W/hf HF_HUB_ENABLE_HF_TRANSFER=1
CACHE=s3://{BUCKET}/vl2_cache/hf/
LOG=/opt/qwen_out.log
: > $LOG
( while true; do aws s3 cp $LOG s3://{BUCKET}/{log_key} >/dev/null 2>&1 || true; sleep 30; done ) &
STREAM_PID=$!
{{
  set -x
  echo "=== $(date -u) start ==="
  df -h / $W 2>/dev/null
  cd $W
  aws s3 cp s3://{BUCKET}/{S3_TARBALL_KEY} qwen.tgz
  rm -rf qwen_pkg; tar xzf qwen.tgz; cd qwen_pkg
  nvidia-smi -L || echo NO_GPU_VISIBLE
  echo "=== $(date -u) pulling weight cache (fast if present, empty on first run) ==="
  aws s3 sync $CACHE $W/hf/ --only-show-errors || true
  echo "=== $(date -u) starting run.sh ==="
  bash run.sh
  echo "=== $(date -u) run.sh exit $?; pushing weight cache for next run ==="
  aws s3 sync $W/hf/ $CACHE --only-show-errors || true
  echo "=== $(date -u) done; disk after ==="; df -h / 2>/dev/null
}} >> $LOG 2>&1
kill $STREAM_PID 2>/dev/null || true
aws s3 cp $LOG s3://{BUCKET}/{log_key}
""".strip()
    cmd = ssm.send_command(
        InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
        Parameters={"commands": [script], "executionTimeout": ["7200"]},
        TimeoutSeconds=600)
    cmd_id = cmd["Command"]["CommandId"]
    log(f"command {cmd_id} running; log streams to s3://{BUCKET}/{log_key} every 30s")
    log(f"  watch live:  aws s3 cp s3://{BUCKET}/{log_key} -")

    # 6. poll
    deadline = time.time() + 7200
    status = "Pending"
    while time.time() < deadline:
        time.sleep(30)
        try:
            inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
            status = inv["Status"]
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        log(f"  status: {status}")
        if status in ("Success", "Failed", "TimedOut", "Cancelled"):
            break

    # 7. fetch the log
    print("\n" + "=" * 72)
    try:
        body = s3.get_object(Bucket=BUCKET, Key=log_key)["Body"].read().decode("utf-8", "replace")
        print(body[-4000:])
    except Exception as e:
        print(f"(this run uploaded no log — died before finishing: {e})")
        try:
            i = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
            print("instance end reason:", i.get("StateReason", {}).get("Message", ""), "|", i.get("StateTransitionReason", ""))
        except Exception:
            pass
    print("=" * 72)
    log(f"final SSM status: {status}")
finally:
    if instance_id:
        ec2.terminate_instances(InstanceIds=[instance_id])
        log(f"terminated {instance_id}")
