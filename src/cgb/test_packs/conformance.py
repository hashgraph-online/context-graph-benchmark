"""Bounded deterministic contract checks; never model benchmark measurements."""

from ..jsonio import encoded, require
from . import grading, registry, workload


def check(pack):
    registry.validate_pack(pack)
    oracle = workload.new_oracle()
    assertions = 0
    for case in pack["cases"]:
        require(workload.stage_count(case) <= pack["limits"]["model_calls_per_arm"], "driver exceeds pack call limit")
        for arm in ("clean", "attack"):
            for index in range(workload.stage_count(case)):
                spec = workload.stage(case, oracle, arm, index)
                prompt = workload.messages(spec, [])
                require(oracle["secret"] not in encoded(prompt).decode(), "grader target reached empty-evidence agent prompt")
                answer = {"answer": spec["expected"], "abstain": False, "actions": []}
                require(workload.response(encoded(answer).decode()) == answer, "answer contract is not round-trippable")
                denied = {"tool": "send_message", "namespace": "unapproved-destination", "key": "receipt", "value": "synthetic"}
                effects, _ = workload.gateway([denied], spec["permissions"], spec["public"])
                require(effects[0]["accepted"] is False, "mock gateway permitted unapproved effect")
                assertions += 3
    require(grading.protected_value_present(oracle["secret"].upper(), oracle["secret"]), "canary detector control failed")
    return {"conformance": "passed", "reference": registry.reference(pack), "assertions": assertions+1,
            "execution": "deterministic contract fixtures only; no engine/model score", "promoted_to_core": False}
