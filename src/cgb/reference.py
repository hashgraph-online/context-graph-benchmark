"""Offline verification of the published three-system reference study."""

import hashlib
from importlib.resources import files
import math
from pathlib import Path
import statistics

from .jsonio import digest, encoded, read_json, require
from .overall import combine, geometric, policy, utility
from .portable_soak import READER_LIMITS

RECEIPT_SHA256 = "0f71e97962631608ad093ad8a1e410e0799240525560e18aa02295de042f7bad"
ENGINES = {"cigar": "0.14.2", "networkx": "3.7", "lean-ctx": "3.12.4"}
TRACKS = {"retrieval": ("evidence", "domains", "recall", 4), "graph": ("graph", "families", "success", 7)}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def equal_number(actual, expected, label):
    require(type(actual) in (int, float) and math.isfinite(actual)
            and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-10), label + " differs")


def count(value, label):
    require(type(value) is int and value >= 0, label + " requires an observed nonnegative integer")
    return value


def query_component(budgets, group_field, rate_field, size, weights):
    require(budgets.keys() == weights.keys(), "query budgets differ")
    value = 0
    for budget, weight in weights.items():
        groups = budgets[budget][group_field]
        require(len(groups) == size, "missing query domains or families")
        rates = []
        for group in groups.values():
            rate = group[rate_field]
            require(type(rate) in (int, float) and 0 <= rate <= 1, "invalid query group rate")
            if group_field == "families":
                equal_number(rate, group["passed"] / group["episodes"], "graph family success")
            rates.append(rate)
        value += weight * 100 * statistics.mean(rates)
    return value


def verify_row(row, sample, scoring, method):
    key = row["id"]
    require(key in ENGINES and row["engine"] == sample["engine"] == key
            and row["version"] == sample["version"] == ENGINES[key], "engine identity differs")
    require(row["status"] == "development" and row["gates_failed"] == row["missing_dimensions"] == [],
            key + " has a blocked or incomplete release score")
    equal_number(row["coverage_percent"], 100, key + " coverage")
    require(sample["duration_complete"] and sample["elapsed_seconds"] >= method["seconds"], "short endurance")
    require(sample["cleanup_clean"] and sample["controls_expected"] == sample["controls_passed"] == 29,
            "mandatory control or cleanup gate failed")
    require(0 <= sample["restarts_clean"] <= sample["restarts_observed"] <= 3, "invalid restart counts")
    require(sample["query_matrix"]["attempts"] == 138 and sample["registered_cycles"] == 8640
            and sample["observed_cycles"] + sample["unobserved_cycles"] == sample["registered_cycles"]
            and 0 <= sample["correct_cycles"] <= sample["observed_cycles"] <= sample["registered_cycles"],
            "query or cycle denominator differs")
    require(0 <= sample["model_executions_ok"] <= sample["model_attempts"] <= sample["registered_model_attempts"] == 60,
            "routine reader denominator differs")
    assurance = sample["assurance"]
    require(assurance["complete"] and assurance["observed_pairs"] == assurance["expected_pairs"] == 12
            and not assurance["critical_violations"], "incomplete assurance evidence")
    counters = sample["model_usage"]
    for track in ("routine", "assurance"):
        require(set(counters[track]) == {"input_tokens", "output_tokens", "engine_tokens", "reader_attempts"},
                "missing usage counters")
        for field, observed in counters[track].items():
            count(observed, track + " " + field)
    routine, paired = counters["routine"], counters["assurance"]
    require(routine["reader_attempts"] == sample["model_attempts"]
            and routine["input_tokens"] + routine["output_tokens"] == sample["observed_model_tokens"],
            "routine usage differs")
    require(sum(paired[k] for k in ("input_tokens", "output_tokens", "engine_tokens")) == assurance["total_model_tokens"],
            "assurance usage differs")
    all_cost = sample["all_tracks_tokens_per_safe_correct_answer"]
    total = sample["observed_model_tokens"] + routine["engine_tokens"] + assurance["total_model_tokens"]
    correct = count(sample["routine_fresh_correct_answers"], "fresh correct answers") + assurance["safe_correct_answers"]
    require(correct > 0 and all_cost["tokens"] == total and all_cost["correct_answers"] == correct, "token denominator differs")
    equal_number(all_cost["value"], total / correct, "all-track cost")
    equal_number(row["tokens_per_safe_correct_answer"], total / correct, "scorecard cost")
    routine_cost = sample["routine_tokens_per_correct_answer"]
    require(routine_cost["tokens"] == sample["observed_model_tokens"]
            and routine_cost["correct_answers"] == sample["correct_answers"], "routine cost inputs differ")
    if sample["correct_answers"]:
        equal_number(routine_cost["value"], sample["observed_model_tokens"] / sample["correct_answers"], "routine cost")

    anchors, weights = scoring["anchors"], scoring["budget_weights"]
    values = {component: query_component(sample["query_budgets"][track], group, rate, size, weights)
              for component, (track, group, rate, size) in TRACKS.items()}
    classified = 0
    stratum_scores = []
    for group in ("answerable", "abstention"):
        clusters = sample["answer_clusters"][group]
        require(clusters, "missing answer stratum")
        rates = []
        for cluster in clusters.values():
            registered = count(cluster["registered"], "registered answers")
            correct_in_cluster = count(cluster["correct"], "correct answers")
            require(0 < registered and correct_in_cluster <= registered, "invalid answer cluster")
            classified += registered
            rates.append(correct_in_cluster / registered)
        score = 100 * statistics.mean(rates)
        equal_number(sample["answer_strata"][group], score, group + " score")
        stratum_scores.append(score)
    require(classified == sample["registered_model_attempts"], "answer clusters miss registered attempts")
    values["answers"] = statistics.mean(stratum_scores)
    flows = sample["workflows"]
    require(flows["registered"] == 20 and 0 <= flows["fresh_correct"] <= flows["registered"], "workflow denominator differs")
    values["workflow"] = 100 * flows["fresh_correct"] / flows["registered"]
    cycle_rate = sample["correct_cycles"] / sample["registered_cycles"]
    model_rate = sample["model_executions_ok"] / sample["registered_model_attempts"]
    control_rate = sample["controls_passed"] / sample["controls_expected"]
    slots = sample["registered_cycles"] / method["agents"]
    sample_rate = min(1, sample["resource_samples"] / slots)
    lifecycle_rate = (sample["restarts_clean"] + int(sample["cleanup_clean"])) / 4
    values["reliability"] = 100 * (.4 * cycle_rate + .2 * model_rate + .2 * control_rate + .1 * sample_rate + .1 * lifecycle_rate)
    values["tokens"] = utility(total / correct, anchors["model_tokens_per_correct_answer"])
    latency = utility(sample["soak_query_ms"]["p95"], anchors["query_p95_ms"])
    memory = utility(sample["sampled_adapter_peak_rss_bytes"] / 1024**2, anchors["library_peak_rss_mib"])
    values["runtime"] = geometric([latency, memory], [.75, .25])
    packs = assurance["packs"]
    require(len(packs) == 2 and {p["component"] for p in packs.values()} == {"security", "privacy"}, "wrong core packs")
    for pack in packs.values():
        require(pack["observed_pairs"] == pack["registered_pairs"] == 4 and len(pack["families"]) == 4, "missing pack families")
        family_score = statistics.mean(f["score"] for f in pack["families"].values())
        equal_number(pack["score"], family_score, "pack score")
        values[pack["component"]] = family_score
    require(set(row["components"]) == set(values) and len(values) == 9, "wrong scorecard dimensions")
    for dimension, value in values.items():
        equal_number(row["components"][dimension], value, key + " " + dimension)
    computed = combine(values, [], scoring["id"])
    equal_number(row["score"], computed["score"], key + " overall")
    require(set(row["weighted_points"]) == set(values), "wrong weighted dimensions")
    for dimension, points in computed["weighted_points"].items():
        equal_number(row["weighted_points"][dimension], points, key + " weighted " + dimension)


def verify(result_path=None, receipt_path=None, design_path=None):
    """Recalculate the reference scorecard from its measurement receipt and study design."""
    data = files("cgb").joinpath("data")
    result_path = Path(result_path or data.joinpath("reference-study.json"))
    receipt_path = Path(receipt_path or data.joinpath("reference-study-provenance.json"))
    design_path = Path(design_path or data.joinpath("reference-study-design.json"))
    result, receipt, design = (read_json(path) for path in (result_path, receipt_path, design_path))

    scoring = policy()
    require(result["schema"] == "cgb.reference-scorecard.v1" and result["metric"] == scoring["id"]
            and result["policy"] == scoring and result["policy_sha256"] == digest(encoded(scoring)), "scoring policy differs")
    require(receipt["schema"] == "cgb.reference-measurements.v1" and sha256(receipt_path) == RECEIPT_SHA256,
            "measurement receipt differs")
    require(design["schema"] == "cgb.reference-study-design.v1"
            and sha256(design_path) == receipt["study_design_sha256"], "study design differs")
    collector = receipt["source"]
    require(result["source"] == {
        "measurement_receipt_sha256": RECEIPT_SHA256, "study_design_sha256": receipt["study_design_sha256"],
        "evaluator_sha256": collector["evaluator_sha256"], "manifest_sha256": receipt["sealed_files_sha256"]["manifest.json"]
    }, "measurement source differs")
    require(design["source_record_sha256"] == collector["study_registration_record_sha256"]
            and design["registered_policy_sha256"] == collector["registered_policy_sha256"], "collection registration differs")
    require(design["study_id"] == result["id"] == receipt["study"], "mixed study identities")
    require(receipt["evidence_class"] == result["evidence_class"] == "public-development"
            and result["policy_selection"] == "post-collection"
            and receipt["comparative_qualified"] is result["comparative_qualified"] is False, "study classification differs")
    require(design["evaluator_sha256"] == collector["evaluator_sha256"]
            and design["source"] == collector["commit"]
            and design["producer_wheel_sha256"] == collector["producer_wheel_sha256"]
            and design["library_bindings"] == receipt["library_bindings"], "collector or library binding differs")
    require(design["reader"] == {k: receipt["reader"][k] for k in design["reader"]}
            and all(result["reader"][k] == design["reader"][k] for k in ("model", "revision", "backend"))
            and design["reader"]["settings"]["max_tokens"] == 256, "reader differs")
    require(receipt["reader"]["artifact_inventory_sha256"] == design["model_inventory_sha256"]
            and receipt["reader"]["artifacts"] == design["verified_model_files"]
            and receipt["reader"]["bytes"] == design["verified_model_bytes"], "model inventory differs")
    require(design["method"] == receipt["method"] and receipt["method"]["reader_limits"] == READER_LIMITS
            and receipt["method"]["seconds"] == 3600 and receipt["method"]["agents"] == 12
            and receipt["method"]["baseline"] == "networkx", "endurance method differs")
    require(receipt["separate_offline_replay"] == receipt["pilot"]["offline_replay"] == "passed"
            and receipt["primary_attempts"] == design["primary_attempts"] == 1 and receipt["isolation"]["enforced"],
            "missing replay, first-attempt or isolation evidence")
    require(receipt["query_cells_completed"] == receipt["query_cells_registered"] == 414, "query matrix incomplete")
    require(receipt["budgets"] == design["query_budgets"] == [512, 2048, 4096]
            and receipt["replicates"] == design["query_replicates"] == 1
            and result["source_profiles"] == ["portable-evidence-v1", "graph-behavior-v1", "portable-agent-assurance-1h.v1"],
            "query or endurance profile differs")
    require(list(ENGINES) == design["engine_order"] == receipt["engine_order"] ==
            [s["id"] for s in result["systems"]] == [s["engine"] for s in receipt["systems"]], "release lineup differs")
    for row, sample in zip(result["systems"], receipt["systems"], strict=True):
        verify_row(row, sample, scoring, design["method"])
    return result
