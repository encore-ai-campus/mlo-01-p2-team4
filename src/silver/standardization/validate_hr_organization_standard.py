import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
try:
    from src.silver.contracts.contract_validator import validate_contract_artifacts
except ModuleNotFoundError:
    sys.path.insert(0, str(BASE_DIR.parents[2]))
    from src.silver.contracts.contract_validator import validate_contract_artifacts

DATA_FILE = (
    BASE_DIR.parents[2]
    / "test"
    / "data"
    / "records.json"
)
OUTPUT_FILE = (
    BASE_DIR
    / "hr-organization-standard-validation.json"
)


def read_csv(filename):
    with (BASE_DIR / filename).open(
        encoding="utf-8-sig",
        newline="",
    ) as file:
        return list(csv.DictReader(file))


with DATA_FILE.open(encoding="utf-8") as file:
    records = json.load(file)

legacy_rows = read_csv("legacy-columns.csv")
word_rows = read_csv("standard-words.csv")
term_rows = read_csv("standard-terms.csv")
mapping_rows = read_csv(
    "hr-organization-column-mapping.csv"
)

naming_text = (
    BASE_DIR / "naming-rules.yaml"
).read_text(encoding="utf-8")

domain_text = (
    BASE_DIR / "data-domains.yaml"
).read_text(encoding="utf-8")


payload_key_sets = {
    frozenset(record["payload"])
    for record in records
}

payload_columns = set().union(*payload_key_sets)

legacy_columns = {
    row["legacy_column"]
    for row in legacy_rows
}

word_ids = {
    row["word_id"]
    for row in word_rows
}


mapped_source_list = [
    row["legacy_column"]
    for row in mapping_rows
]

mapped_source_counts = Counter(
    mapped_source_list
)

mapped_sources = set(
    mapped_source_list
)


term_source_list = [
    source
    for row in term_rows
    for source in row["source_columns"].split("|")
    if source
]

term_source_counts = Counter(
    term_source_list
)

term_sources = set(
    term_source_list
)


used_word_ids = {
    word_id
    for row in term_rows
    for word_id in row["word_ids"].split("|")
    if word_id
}

used_domain_ids = {
    row["domain_id"]
    for row in term_rows
}


pattern_match = re.search(
    r"""^\s*physical_name_pattern:\s*['"](.+)['"]\s*$""",
    naming_text,
    re.MULTILINE,
)

if pattern_match is None:
    raise ValueError(
        "naming-rules.yaml에서 "
        "physical_name_pattern을 찾지 못했습니다."
    )

physical_name_pattern = pattern_match.group(1)


domain_ids = set(
    re.findall(
        r"^\s*- domain_id:\s*([A-Z0-9_]+)\s*$",
        domain_text,
        re.MULTILINE,
    )
)


term_ids = [
    row["term_id"]
    for row in term_rows
]

logical_terms = [
    row["logical_term"]
    for row in term_rows
]

physical_names = [
    row["physical_name"]
    for row in term_rows
]

mapping_targets = [
    row["standard_column"]
    for row in mapping_rows
]

term_by_physical = {
    row["physical_name"]: row
    for row in term_rows
}


selected_word_ids = {
    "AREA",
    "EMPLOYEE",
    "DATETIME",
    "STATUS",
    "CODE",
    "REGISTRATION",
}

contract_errors = validate_contract_artifacts(BASE_DIR.parents[2])

deprecated_word_ids = {
    "ORGANIZATION",
    "MANAGER",
}


checks = {
    "payload_record_count":
        len(records),

    "payload_column_count":
        len(payload_columns),

    "all_payload_key_sets_equal":
        len(payload_key_sets) == 1,

    "legacy_column_count":
        len(legacy_rows),

    "legacy_columns_match_payload":
        legacy_columns == payload_columns,

    "standard_word_count":
        len(word_rows),

    "standard_term_count":
        len(term_rows),

    "mapping_count":
        len(mapping_rows),

    "all_legacy_columns_in_terms":
        legacy_columns == term_sources,

    "each_legacy_column_in_one_term":
        all(
            term_source_counts[column] == 1
            for column in legacy_columns
        ),

    "all_legacy_columns_mapped":
        legacy_columns == mapped_sources,

    "each_legacy_column_mapped_once":
        all(
            mapped_source_counts[column] == 1
            for column in legacy_columns
        ),

    "mapping_targets_match_terms":
        set(mapping_targets) == set(physical_names),

    "mapping_domains_match_terms":
        all(
            row["standard_column"]
            in term_by_physical
            and row["domain_id"]
            == term_by_physical[
                row["standard_column"]
            ]["domain_id"]
            for row in mapping_rows
        ),

    "mapping_logical_terms_match":
        all(
            row["standard_column"]
            in term_by_physical
            and row["logical_term"]
            == term_by_physical[
                row["standard_column"]
            ]["logical_term"]
            for row in mapping_rows
        ),

    "mapping_required_matches_nullable":
        all(
            row["standard_column"]
            in term_by_physical
            and row["required"]
            == (
                "Y"
                if term_by_physical[
                    row["standard_column"]
                ]["nullable"] == "N"
                else "N"
            )
            for row in mapping_rows
        ),

    "all_term_words_exist":
        used_word_ids <= word_ids,

    "all_term_domains_exist":
        used_domain_ids <= domain_ids,

    "selected_vocabulary_is_used":
        selected_word_ids <= used_word_ids,

    "deprecated_vocabulary_is_not_used":
        not bool(
            deprecated_word_ids
            & used_word_ids
        ),

    "all_physical_names_follow_rule":
        all(
            re.fullmatch(
                physical_name_pattern,
                name,
            )
            for name in physical_names
        ),

    "term_ids_are_unique":
        len(term_ids) == len(set(term_ids)),

    "logical_terms_are_unique":
        len(logical_terms)
        == len(set(logical_terms)),

    "physical_names_are_unique":
        len(physical_names)
        == len(set(physical_names)),

    "mapping_targets_are_unique":
        len(mapping_targets)
        == len(set(mapping_targets)),

    "term_ids_match_physical_names":
        all(
            row["term_id"]
            == row["physical_name"].upper()
            for row in term_rows
        ),

    "identifier_suffixes_valid":
        all(
            "ID"
            not in row["word_ids"].split("|")
            or row["physical_name"].endswith(
                "_id"
            )
            for row in term_rows
        ),

    "code_suffixes_valid":
        all(
            "CODE"
            not in row["word_ids"].split("|")
            or row["physical_name"].endswith(
                "_code"
            )
            for row in term_rows
        ),

    "date_suffixes_valid":
        all(
            "DATE"
            not in row["word_ids"].split("|")
            or row["physical_name"].endswith(
                "_date"
            )
            for row in term_rows
        ),

    "datetime_suffixes_valid":
        all(
            "DATETIME"
            not in row["word_ids"].split("|")
            or row["physical_name"].endswith(
                "_datetime"
            )
            for row in term_rows
        ),

    "status_code_domain_is_defined":
        (
            "STATUS_CODE_20" in domain_ids
            and re.search(
                r"^\s*- ACTIVE\s*$",
                domain_text,
                re.MULTILINE,
            ) is not None
            and re.search(
                r"^\s*- INACTIVE\s*$",
                domain_text,
                re.MULTILINE,
            ) is not None
        ),

    "status_code_term_name_is_valid":
        all(
            row["domain_id"] != "STATUS_CODE_20"
            or row["physical_name"].endswith(
                "_status_code"
            )
            for row in term_rows
    ),

    "contract_artifacts_are_consistent":
        not contract_errors,
}


errors = [
    name
    for name, result in checks.items()
    if isinstance(result, bool)
    and not result
]


unused_standard_words = sorted(
    word_ids - used_word_ids
)

warnings = []

if unused_standard_words:
    warnings.append({
        "unused_standard_words":
            unused_standard_words
    })


report = {
    "validation_name":
        "HR area standard metadata validation",

    "validator":
        Path(__file__).name,

    "status":
        "ready" if not errors else "blocked",

    "checks":
        checks,

    "errors":
        errors + (["CONTRACT_ARTIFACTS_INCONSISTENT"] if contract_errors else []),

    "contract_errors": list(contract_errors),

    "warnings":
        warnings,

    "artifacts": [
        "legacy-columns.csv",
        "standard-words.csv",
        "standard-terms.csv",
        "naming-rules.yaml",
        "data-domains.yaml",
        "hr-organization-column-mapping.csv",
        "hr-organization-standard-validation.json",
    ],
}


with OUTPUT_FILE.open(
    "w",
    encoding="utf-8",
) as file:
    json.dump(
        report,
        file,
        ensure_ascii=False,
        indent=2,
    )
    file.write("\n")


print(
    json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
    )
)
