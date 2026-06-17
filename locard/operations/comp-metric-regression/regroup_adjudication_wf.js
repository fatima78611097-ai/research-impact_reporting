export const meta = {
  name: 'regroup-adjudication',
  description: 'Vision-adjudicate 13 disputed regroup pairings against the actual page',
  phases: [{ title: 'Adjudicate', detail: '13 Opus agents read pages, rule whose label is true' }],
}
const CASES = [{"id": "752d2b78:1", "kind": "regress?", "value": "3375", "model_subject": "Individuals helped", "regroup_label": "Age 59 or older", "img": "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/reviewbox/752d2b78_1.png", "gate_now": "publish", "statement": "ACTS helped 3,375 individuals through its programs in 2022."}, {"id": "95f2054c:5", "kind": "regress?", "value": "30", "model_subject": "Years of operation", "regroup_label": "ANNUAL REPORT", "img": "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/reviewbox/95f2054c_5.png", "gate_now": "quarantine", "statement": "Community Options celebrated its 30th anniversary in 2019."}, {"id": "b42e6f95:8", "kind": "regress?", "value": "258", "model_subject": "Incarcerated Grief Support recipients", "regroup_label": "detained individuals received", "img": "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/reviewbox/b42e6f95_8.png", "gate_now": "publish", "statement": "258 detained individuals received Incarcerated Grief Support."}, {"id": "be50ade6:2", "kind": "regress?", "value": "154", "model_subject": "Achievers served", "regroup_label": "Professional Mentorship", "img": "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/reviewbox/be50ade6_2.png", "gate_now": "publish", "statement": "154 Achievers received one-on-one Professional Mentorship."}, {"id": "be50ade6:5", "kind": "regress?", "value": "100", "model_subject": "Caregiver recommendation rate", "regroup_label": "of Achievers say they feel HOPEFUL ABOUT THEIR FUTURE", "img": "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/reviewbox/be50ade6_5.png", "gate_now": "publish", "statement": "100% of caregivers say they would recommend the Friends-Boston program."}, {"id": "be50ade6:8", "kind": "regress?", "value": "45", "model_subject": "Family crises addressed", "regroup_label": "1:1 IN DEPTH SUPPORT", "img": "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/reviewbox/be50ade6_8.png", "gate_now": "publish", "statement": "45 family crisis situations were addressed through 1:1 in-depth support."}, {"id": "d9fef44f:1", "kind": "regress?", "value": "638", "model_subject": "PSH individuals housed", "regroup_label": "271 of which were children", "img": "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/reviewbox/d9fef44f_1.png", "gate_now": "publish", "statement": "Facing Forward's Permanent Supportive Housing program housed 638 individuals, including 271 children"}, {"id": "ee3f949b:5", "kind": "regress?", "value": "1329", "model_subject": "Referrals to services", "regroup_label": "Continued support and resources through follow-up", "img": "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/reviewbox/ee3f949b_5.png", "gate_now": "publish", "statement": "St. Martha's Hall made 1,329 referrals to other services for its residents."}, {"id": "5b8eae73:5", "kind": "missed?", "value": "83018", "model_subject": "HUB event sales", "regroup_label": "in Event Sales", "img": "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/reviewbox/5b8eae73_5.png", "gate_now": "publish", "statement": "The HUB generated $83,018 in event sales in 2023."}, {"id": "8bf02961:6", "kind": "missed?", "value": "11", "model_subject": "Staff retention increase", "regroup_label": "growth rate, with 324 staff on board by the end of FY24", "img": "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/reviewbox/8bf02961_6.png", "gate_now": "quarantine", "statement": "Staff retention increased by 11%."}, {"id": "9d9ae097:1", "kind": "missed?", "value": "25571", "model_subject": "Total families assisted", "regroup_label": "days of food for a total of for Irving families.", "img": "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/reviewbox/9d9ae097_1.png", "gate_now": "publish", "statement": "Irving Cares assisted 25,571 Irving families during the reporting period."}, {"id": "b42e6f95:9", "kind": "missed?", "value": "180", "model_subject": "Co-Parenting Seminar attendees", "regroup_label": "individuals attended Divorce Support Groups", "img": "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/reviewbox/b42e6f95_9.png", "gate_now": "publish", "statement": "180 parents attended the Co-Parenting Seminar."}, {"id": "ec055971:3", "kind": "missed?", "value": "1660", "model_subject": "Behavioral health served", "regroup_label": "Total people served: 6,670", "img": "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/reviewbox/ec055971_3.png", "gate_now": "publish", "statement": "Behavioral Health & Clinical Services served 1,660 people."}];
const SCHEMA = {
  type: 'object',
  properties: {
    true_label: { type: 'string', description: 'the label/caption that genuinely belongs to this number ON THE PAGE, verbatim as printed' },
    verdict: { type: 'string', enum: ['MODEL', 'REGROUP', 'BOTH_SAME', 'NEITHER'] },
    reason: { type: 'string' },
  },
  required: ['true_label', 'verdict', 'reason'],
};
phase('Adjudicate');
const out = await parallel(CASES.map(c => () =>
  agent(`Read this nonprofit-report page image with your Read tool: ${c.img}

Find the printed number ${c.value} on the page (it may have a red box). Determine which caption/label genuinely belongs to it per the page LAYOUT.

Two candidate subjects are in dispute:
- MODEL says: "${c.model_subject}"
- REGROUP (a geometry algorithm) says: "${c.regroup_label}"

Rule INDEPENDENTLY from the page:
- verdict MODEL if the model's subject is what the page pairs with this number and regroup's is wrong
- verdict REGROUP if regroup's label is what the page pairs (model's is wrong or a misparaphrase)
- verdict BOTH_SAME if they describe the SAME pairing in different words (paraphrase)
- verdict NEITHER if the number's true label is something else entirely
Also return the true label verbatim.`,
    { label: `adj:${c.id}`, phase: 'Adjudicate', model: 'opus', schema: SCHEMA })
    .then(v => ({ id: c.id, kind: c.kind, ...v }))
));
return out.filter(Boolean);
