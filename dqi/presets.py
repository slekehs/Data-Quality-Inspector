"""Named rule presets for evaluation datasets."""

HELPSTEER2_ATTRIBUTES = ("helpfulness", "correctness", "coherence", "complexity", "verbosity")
HELPSTEER2 = {
    "required": ["response"],
    "unique_id": False,
    "ratings": {
        attribute: {"required": True, "numeric": True, "integer": True, "range": [0, 4]}
        for attribute in HELPSTEER2_ATTRIBUTES
    },
}
