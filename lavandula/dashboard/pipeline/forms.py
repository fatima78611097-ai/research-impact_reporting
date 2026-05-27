import datetime
import re
from pathlib import Path

from django import forms

from .orchestrator import US_STATES

STATE_CHOICES = [(s, s) for s in US_STATES]
PHASE_CHOICES = [("seed", "Seed"), ("resolve", "Resolve")]

_SELECT = "w-full border border-gray-300 rounded px-3 py-2"

LLM_PRESETS = {
    "deepseek-v4-flash": {
        "llm_url": "https://api.deepseek.com/v1",
        "llm_model": "deepseek-v4-flash",
        "llm_api_key_ssm": "lavandula/deepseek/api_key",
    },
    "local-ollama": {
        "llm_url": "http://localhost:11434/v1",
        "llm_model": "gemma4:e4b",
    },
}

LLM_PRESET_CHOICES = [
    ("deepseek-v4-flash", "DeepSeek v4-flash (API)"),
    ("local-ollama", "Local Ollama (gemma4)"),
]

SEARCH_ENGINE_CHOICES = [
    ("brave", "Brave (default)"),
    ("google", "Google"),
    ("brave_google", "Brave + Google"),
    ("auto", "Auto (Serpex routing)"),
]


ALL_NTEE_MAJORS = "A,B,C,D,E,F,G,H,I,J,K,L,M,N,O,P,Q,R,S,T,U,V,W,X,Y,Z"


class RunStateForm(forms.Form):
    state_codes = forms.MultipleChoiceField(
        choices=STATE_CHOICES,
        widget=forms.SelectMultiple(attrs={"class": _SELECT, "size": "6"}),
    )
    phases = forms.MultipleChoiceField(
        choices=PHASE_CHOICES,
        initial=["seed", "resolve"],
        widget=forms.CheckboxSelectMultiple,
    )
    ntee_majors = forms.CharField(
        initial=ALL_NTEE_MAJORS,
        widget=forms.TextInput(attrs={"class": _SELECT}),
        label="NTEE Majors",
        help_text="Comma-separated letter codes",
    )
    revenue_min = forms.IntegerField(
        initial=500000,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": _SELECT}),
        label="Revenue Min",
    )
    revenue_max = forms.IntegerField(
        initial=999999999999,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": _SELECT}),
        label="Revenue Max",
    )
    target = forms.IntegerField(
        initial=999999,
        min_value=1, max_value=999999,
        widget=forms.NumberInput(attrs={"class": _SELECT}),
        label="Target",
    )
    llm_preset = forms.ChoiceField(
        choices=LLM_PRESET_CHOICES,
        initial="deepseek-v4-flash",
        widget=forms.Select(attrs={"class": _SELECT}),
        label="LLM (resolve phase)",
    )
    brave_qps = forms.FloatField(required=False, min_value=0.1, max_value=50.0, widget=forms.NumberInput(
        attrs={"class": _SELECT, "step": "0.1"}
    ))
    consumer_threads = forms.IntegerField(required=False, min_value=1, max_value=16, widget=forms.NumberInput(
        attrs={"class": _SELECT}
    ))
    search_parallelism = forms.IntegerField(required=False, min_value=1, max_value=32, widget=forms.NumberInput(
        attrs={"class": _SELECT}
    ))
    limit = forms.IntegerField(required=False, min_value=0, max_value=999999, widget=forms.NumberInput(
        attrs={"class": _SELECT}
    ))


class RunCrawlForm(forms.Form):
    state = forms.ChoiceField(
        choices=[("", "— All states —")] + STATE_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": _SELECT}),
    )
    archive = forms.CharField(required=False, widget=forms.TextInput(
        attrs={"class": "w-full border border-gray-300 rounded px-3 py-2", "placeholder": "s3://bucket/path"}
    ))
    limit = forms.IntegerField(required=False, min_value=0, max_value=999999, widget=forms.NumberInput(
        attrs={"class": "w-full border border-gray-300 rounded px-3 py-2"}
    ))
    max_concurrent_orgs = forms.IntegerField(required=False, min_value=1, max_value=500, widget=forms.NumberInput(
        attrs={"class": "w-full border border-gray-300 rounded px-3 py-2"}
    ))
    max_download_workers = forms.IntegerField(required=False, min_value=1, max_value=100, widget=forms.NumberInput(
        attrs={"class": "w-full border border-gray-300 rounded px-3 py-2"}
    ))
    async_mode = forms.BooleanField(required=False, label="Async")
    classifier_backend = forms.ChoiceField(
        choices=[
            ("deepseek", "DeepSeek (API, default)"),
            ("gemini", "Gemini (CLI)"),
            ("claude", "Claude (CLI)"),
        ],
        initial="deepseek",
        required=False,
        widget=forms.Select(attrs={"class": _SELECT}),
        label="Classifier",
    )
    refresh = forms.BooleanField(
        required=False,
        label="Refresh (re-crawl existing orgs)",
    )
    skip_existing = forms.BooleanField(
        required=False,
        label="Skip existing PDFs (use with Refresh)",
    )


class ResolverForm(forms.Form):
    state = forms.ChoiceField(
        choices=[("", "— Select state —")] + STATE_CHOICES,
        widget=forms.Select(attrs={"class": _SELECT}),
    )
    llm_preset = forms.ChoiceField(
        choices=LLM_PRESET_CHOICES,
        initial="deepseek-v4-flash",
        widget=forms.Select(attrs={"class": _SELECT}),
        label="LLM",
    )
    search_engines = forms.ChoiceField(
        choices=SEARCH_ENGINE_CHOICES,
        initial="brave",
        widget=forms.Select(attrs={"class": _SELECT}),
        label="Search Engine",
    )
    brave_qps = forms.FloatField(initial=10.0, required=False, min_value=0.1, max_value=50.0, widget=forms.NumberInput(
        attrs={"class": _SELECT, "step": "0.1"}
    ), label="Search QPS")
    search_parallelism = forms.IntegerField(initial=12, required=False, min_value=1, max_value=32, widget=forms.NumberInput(
        attrs={"class": _SELECT}
    ))
    consumer_threads = forms.IntegerField(initial=4, required=False, min_value=1, max_value=16, widget=forms.NumberInput(
        attrs={"class": _SELECT}
    ))
    limit = forms.IntegerField(required=False, min_value=0, max_value=999999, widget=forms.NumberInput(
        attrs={"class": _SELECT}
    ))
    fresh_only = forms.BooleanField(required=False)


class CrawlerForm(forms.Form):
    archive = forms.CharField(required=False, widget=forms.TextInput(
        attrs={"class": "w-full border border-gray-300 rounded px-3 py-2", "placeholder": "s3://bucket/path"}
    ))
    limit = forms.IntegerField(required=False, min_value=0, max_value=999999, widget=forms.NumberInput(
        attrs={"class": "w-full border border-gray-300 rounded px-3 py-2"}
    ))
    max_concurrent_orgs = forms.IntegerField(required=False, min_value=1, max_value=500, widget=forms.NumberInput(
        attrs={"class": "w-full border border-gray-300 rounded px-3 py-2"}
    ))
    max_download_workers = forms.IntegerField(required=False, min_value=1, max_value=100, widget=forms.NumberInput(
        attrs={"class": "w-full border border-gray-300 rounded px-3 py-2"}
    ))


def _get_definition_choices():
    """Scan definitions/ directory for available .md files."""
    defn_dir = Path(__file__).resolve().parents[2] / "nonprofits" / "definitions"
    choices = []
    if defn_dir.is_dir():
        for f in sorted(defn_dir.glob("*.md")):
            if re.match(r"^[a-z][a-z0-9_]*$", f.stem):
                choices.append((f.stem, f.stem))
    if not choices:
        choices = [("corpus_reports", "corpus_reports")]
    return choices


class ClassifierForm(forms.Form):
    state = forms.ChoiceField(
        choices=[("", "All states")] + STATE_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": _SELECT}),
    )
    llm_preset = forms.ChoiceField(
        choices=LLM_PRESET_CHOICES,
        initial="deepseek-v4-flash",
        widget=forms.Select(attrs={"class": _SELECT}),
        label="LLM",
    )
    definition = forms.ChoiceField(
        choices=_get_definition_choices,
        initial="corpus_reports",
        widget=forms.Select(attrs={"class": _SELECT}),
        label="Definition",
    )
    limit = forms.IntegerField(required=False, min_value=0, max_value=999999, widget=forms.NumberInput(
        attrs={"class": _SELECT}
    ))
    re_classify = forms.BooleanField(required=False, label="Re-classify")


def _clean_990_common(cleaned_data):
    """Shared validation for 990 index/parse forms."""
    ein = cleaned_data.get("ein")
    if ein and not re.match(r"^\d{9}$", ein):
        raise forms.ValidationError("EIN must be exactly 9 digits.")
    years_str = cleaned_data.get("years", "").strip()
    if years_str:
        if not re.match(r"^\d{4}(\s*,\s*\d{4})*$", years_str):
            raise forms.ValidationError("Years must be comma-separated 4-digit years.")
        year_list = [int(y.strip()) for y in years_str.split(",")]
        current_year = datetime.date.today().year
        for y in year_list:
            if y < 2017 or y > current_year:
                raise forms.ValidationError(
                    f"Year {y} outside valid range [2017, {current_year}]."
                )
        if len(year_list) > 10:
            raise forms.ValidationError("Maximum 10 years per request.")
    return cleaned_data


class EnrichIndexForm(forms.Form):
    ein = forms.CharField(
        max_length=9, required=False,
        widget=forms.TextInput(attrs={"class": _SELECT, "placeholder": "EIN (optional)"}),
    )
    years = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": _SELECT, "placeholder": "2024,2025 (optional)"}),
    )

    def clean(self):
        return _clean_990_common(super().clean())


class EnrichParseForm(forms.Form):
    ein = forms.CharField(
        max_length=9, required=False,
        widget=forms.TextInput(attrs={"class": _SELECT, "placeholder": "EIN (optional)"}),
    )
    reparse = forms.BooleanField(required=False, label="Reparse errors")
    backfill = forms.BooleanField(required=False, label="Backfill (all unprocessed)")

    def clean(self):
        return _clean_990_common(super().clean())


class PhoneEnrichForm(forms.Form):
    state = forms.ChoiceField(
        choices=[("", "All resolved")] + STATE_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": _SELECT}),
    )
    limit = forms.IntegerField(required=False, min_value=0, max_value=999999,
        widget=forms.NumberInput(attrs={"class": _SELECT}))
    search_engines = forms.ChoiceField(
        choices=SEARCH_ENGINE_CHOICES, initial="brave",
        widget=forms.Select(attrs={"class": _SELECT}),
        label="Search Engine",
    )


BACKEND_CHOICES = [
    ("deepseek", "DeepSeek"),
    ("claude", "Claude (Haiku)"),
    ("gemini", "Gemini"),
    ("codex", "Codex"),
]


class ExtractContextForm(forms.Form):
    state = forms.ChoiceField(choices=[("", "All states")] + STATE_CHOICES, required=False,
        widget=forms.Select(attrs={"class": _SELECT}), label="State filter")
    ein = forms.CharField(max_length=20, required=False,
        widget=forms.TextInput(attrs={"class": _SELECT, "placeholder": "e.g. 13-1234567"}),
        label="Single EIN")
    limit = forms.IntegerField(required=False, min_value=1, max_value=999999,
        widget=forms.NumberInput(attrs={"class": _SELECT}))
    reextract = forms.BooleanField(required=False, label="Re-extract existing")
    download_workers = forms.IntegerField(initial=8, required=False, min_value=1, max_value=32,
        widget=forms.NumberInput(attrs={"class": _SELECT}), label="Download workers")
    extract_workers = forms.IntegerField(initial=4, required=False, min_value=1, max_value=32,
        widget=forms.NumberInput(attrs={"class": _SELECT}), label="Extract workers")


class ReclassifyForm(forms.Form):
    run_tag = forms.CharField(max_length=100,
        widget=forms.TextInput(attrs={"class": _SELECT, "placeholder": "e.g. v3-deepseek-20260509"}),
        label="Run tag")
    backend = forms.ChoiceField(choices=BACKEND_CHOICES, initial="deepseek",
        widget=forms.Select(attrs={"class": _SELECT}))
    definition = forms.ChoiceField(choices=_get_definition_choices, initial="corpus_reports",
        widget=forms.Select(attrs={"class": _SELECT}), label="Definition", required=False)
    state = forms.ChoiceField(choices=[("", "All states")] + STATE_CHOICES, required=False,
        widget=forms.Select(attrs={"class": _SELECT}), label="State filter")
    ein = forms.CharField(max_length=20, required=False,
        widget=forms.TextInput(attrs={"class": _SELECT, "placeholder": "e.g. 13-1234567"}),
        label="Single EIN")
    sample = forms.IntegerField(required=False, min_value=1, max_value=999999,
        widget=forms.NumberInput(attrs={"class": _SELECT}), label="Sample size")
    workers = forms.IntegerField(initial=4, required=False, min_value=1, max_value=32,
        widget=forms.NumberInput(attrs={"class": _SELECT}))
    allow_fallback = forms.BooleanField(required=False, label="Allow fallback text")
    min_text_len = forms.IntegerField(initial=1000, required=False, min_value=0, max_value=10000,
        widget=forms.NumberInput(attrs={"class": _SELECT}), label="Min text length")
    dry_run = forms.BooleanField(required=False, label="Dry run")
    resume = forms.BooleanField(required=False, label="Resume")


class ResolveDisagreementsForm(forms.Form):
    run_tag = forms.CharField(max_length=100,
        widget=forms.TextInput(attrs={"class": _SELECT, "placeholder": "Run tag with disagreements"}),
        label="Run tag")
    backend = forms.ChoiceField(choices=BACKEND_CHOICES, initial="claude",
        widget=forms.Select(attrs={"class": _SELECT}), label="Tiebreaker backend")
    definition = forms.ChoiceField(choices=_get_definition_choices, initial="corpus_reports",
        widget=forms.Select(attrs={"class": _SELECT}), label="Definition", required=False)
    state = forms.ChoiceField(choices=[("", "All states")] + STATE_CHOICES, required=False,
        widget=forms.Select(attrs={"class": _SELECT}), label="State filter")
    sample = forms.IntegerField(required=False, min_value=1, max_value=999999,
        widget=forms.NumberInput(attrs={"class": _SELECT}), label="Sample size")
    workers = forms.IntegerField(initial=4, required=False, min_value=1, max_value=32,
        widget=forms.NumberInput(attrs={"class": _SELECT}))
    dry_run = forms.BooleanField(required=False, label="Dry run")


class CompareClassifyForm(forms.Form):
    run_tag = forms.CharField(max_length=100,
        widget=forms.TextInput(attrs={"class": _SELECT, "placeholder": "Run tag to compare"}),
        label="Run tag")
    state = forms.ChoiceField(choices=[("", "All states")] + STATE_CHOICES, required=False,
        widget=forms.Select(attrs={"class": _SELECT}), label="State filter")
    show_reasoning = forms.BooleanField(required=False, label="Show reasoning")


class PromoteClassifyForm(forms.Form):
    run_tag = forms.CharField(max_length=100,
        widget=forms.TextInput(attrs={"class": _SELECT, "placeholder": "Run tag to promote"}),
        label="Run tag")
    state = forms.ChoiceField(choices=[("", "All states")] + STATE_CHOICES, required=False,
        widget=forms.Select(attrs={"class": _SELECT}), label="State filter")
    confirm = forms.BooleanField(required=False, label="Confirm promotion")


INSTANCE_TYPE_CHOICES = [
    ("g6.2xlarge", "g6.2xlarge (1 GPU, $0.60/hr spot)"),
    ("g6.4xlarge", "g6.4xlarge (1 GPU, $1.01/hr spot)"),
    ("g6.8xlarge", "g6.8xlarge (1 GPU, $1.61/hr spot)"),
]

CLASSIFICATION_CHOICES = [
    ("annual,impact", "Annual + Impact (default)"),
    ("annual,impact,hybrid", "Annual + Impact + Hybrid"),
    ("annual", "Annual only"),
    ("impact", "Impact only"),
]


class ParseRunForm(forms.Form):
    run_tag = forms.CharField(
        max_length=64,
        widget=forms.TextInput(attrs={
            "class": _SELECT,
            "placeholder": "e.g. edu-b1, national-v2",
            "pattern": r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$",
        }),
        label="Run Tag",
    )
    ntee = forms.CharField(
        max_length=10, required=False,
        widget=forms.TextInput(attrs={
            "class": _SELECT,
            "placeholder": "e.g. B% (blank = all)",
        }),
        label="NTEE Filter",
    )
    priority = forms.ChoiceField(
        choices=CLASSIFICATION_CHOICES,
        initial="annual,impact",
        widget=forms.Select(attrs={"class": _SELECT}),
        label="Classifications",
    )
    instance_type = forms.ChoiceField(
        choices=INSTANCE_TYPE_CHOICES,
        initial="g6.2xlarge",
        widget=forms.Select(attrs={"class": _SELECT}),
        label="Instance Type",
    )
    no_spot = forms.BooleanField(
        required=False,
        label="Use on-demand (not spot)",
    )
    ami_id = forms.CharField(
        required=False, max_length=25,
        widget=forms.Select(attrs={"class": _SELECT}),
        label="AMI",
    )
    max_hours = forms.IntegerField(
        initial=24, min_value=1, max_value=24,
        widget=forms.NumberInput(attrs={"class": _SELECT}),
        label="Max Hours",
    )
    batch_size = forms.IntegerField(
        initial=500, min_value=10, max_value=5000,
        widget=forms.NumberInput(attrs={"class": _SELECT}),
        label="Batch Size",
    )
    max_docs = forms.IntegerField(
        required=False, min_value=1, max_value=999999,
        widget=forms.NumberInput(attrs={"class": _SELECT, "placeholder": "blank = all eligible"}),
        label="Max Documents",
    )
    retry_errors = forms.BooleanField(
        required=False,
        label="Retry previous errors",
    )
    start_at = forms.CharField(
        required=False, max_length=16,
        widget=forms.TextInput(attrs={
            "class": _SELECT,
            "type": "datetime-local",
        }),
        label="Start At (UTC)",
        help_text="Leave blank to launch immediately",
    )
    capacity_wait_hours = forms.IntegerField(
        initial=1, min_value=1, max_value=12, required=False,
        widget=forms.NumberInput(attrs={"class": _SELECT}),
        label="Capacity Wait (hours)",
        help_text="How long to retry if no spot capacity",
    )

    def clean_run_tag(self):
        tag = self.cleaned_data["run_tag"]
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", tag):
            raise forms.ValidationError("Run tag must be alphanumeric with hyphens/underscores")
        return tag

    def clean_ntee(self):
        ntee = self.cleaned_data.get("ntee", "").strip()
        if ntee and not re.match(r"^[A-Z][A-Z0-9%]*$", ntee):
            raise forms.ValidationError("NTEE filter must start with a capital letter (e.g. B%, P2%)")
        return ntee or None

    def clean_ami_id(self):
        ami = self.cleaned_data.get("ami_id", "").strip()
        if ami and not re.match(r"^ami-[a-f0-9]{8,17}$", ami):
            raise forms.ValidationError("Invalid AMI ID format")
        return ami or None
