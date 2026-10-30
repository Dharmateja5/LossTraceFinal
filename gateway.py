import json
from pathlib import Path, PurePosixPath
import re

import hashlib
import pymupdf
from fnmatch import fnmatchcase

class RuntimeGateway:
    """
    Controlled read access to the LossTrace application runtime.

    The caller asks for business information.
    The caller does not choose arbitrary filesystem paths.
    """

    def __init__(
        self,
        runtime_root: str = "data/application_runtime",
    ):
        self.runtime_root = Path(runtime_root)

    def _load_record(
        self,
        dossier_id: str,
        filename: str,
    ) -> dict:
        """Read a manifest-registered JSON record with SHA-256 verification.

        A resolved path stays inside the dossier; an unregistered, duplicated,
        or modified record fails closed before its content reaches an agent.
        """
        if not re.fullmatch(r"d-[0-9a-f]{12}", dossier_id):
            raise ValueError("Invalid dossier ID")
        if filename not in {"claim.json", "policy.json", "ledger.json", "site_observations.json"}:
            raise ValueError("Unauthorized record name")

        root = self.runtime_root.resolve()
        dossier_root = (root / "world" / dossier_id).resolve()
        target = f"world/{dossier_id}/records/{filename}"
        manifest_path = root / "manifests" / f"{dossier_id}.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        matches = [item for item in manifest.get("artifacts", [])
                   if item.get("path") == target
                   and item.get("media_type") == "application/json"]
        if len(matches) != 1:
            raise ValueError("Record missing or ambiguous in trusted manifest")
        path = (root / target).resolve()
        if not dossier_root.is_relative_to(root) or not path.is_relative_to(dossier_root):
            raise ValueError("Record path escapes dossier root")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != matches[0].get("sha256"):
            raise ValueError("Record SHA-256 does not match manifest")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("Expected JSON object in registered record")
        return value

    def get_claim_record(
        self,
        dossier_id: str,
    ) -> dict:
        return self._load_record(
            dossier_id,
            "claim.json",
        )

    def get_policy_record(
        self,
        dossier_id: str,
    ) -> dict:
        return self._load_record(
            dossier_id,
            "policy.json",
        )

    def get_ledger_record(
        self,
        dossier_id: str,
    ) -> dict:
        return self._load_record(
            dossier_id,
            "ledger.json",
        )


    def get_site_observations(
        self,
        dossier_id: str,
    ) -> dict:
        return self._load_record(
            dossier_id,
            "site_observations.json",
        )

    def get_policy_wording(
        self,
        dossier_id: str,
    ) -> dict:
        return self._read_report_pdf(
            dossier_id,
            "get_policy_wording",
        )

    def _read_report_pdf(
        self,
        dossier_id: str,
        tool_name: str,
    ) -> dict:
        """Retrieve a contract-authorized, manifest-verified PDF."""

        if not re.fullmatch(r"d-[0-9a-f]{12}", dossier_id):
            raise ValueError("Invalid dossier ID")

        # 1. Read tool contract
        contract_path = (
            Path(__file__).resolve().parent
            / "data_contract"
            / "tool_contracts.json"
        )

        contract = json.loads(
            contract_path.read_text(encoding="utf-8")
        )

        # 2. Resolve authorized document path
        matches = [
            tool for tool in contract["tools"]
            if tool.get("name") == tool_name
            and tool.get("operation") == "READ_ARTIFACT"
        ]

        if len(matches) != 1:
            raise ValueError(
                f"No unique READ_ARTIFACT contract for {tool_name}"
            )

        relative_path = matches[0].get("relative_path")

        if not isinstance(relative_path, str):
            raise ValueError("Invalid document path in tool contract")

        relative = PurePosixPath(relative_path)

        if (
            relative.is_absolute()
            or "\\" in relative_path
            or ".." in relative.parts
            or len(relative.parts) < 2
            or relative.parts[0] not in {"evidence", "policy"}
            or relative.suffix.lower() != ".pdf"
        ):
            raise ValueError("Unauthorized document path")

        # 3. Load manifest
        root = self.runtime_root.resolve()

        manifest = json.loads(
            (root / "manifests" / f"{dossier_id}.json")
            .read_text(encoding="utf-8")
        )

        target = (
            PurePosixPath("world") / dossier_id / relative
        ).as_posix()

        # 4. Find matching authorized artifact
        artifacts = [
            item for item in manifest["artifacts"]
            if item.get("path") == target
            and item.get("media_type") == "application/pdf"
        ]

        if len(artifacts) != 1:
            raise FileNotFoundError(
                f"Authorized PDF not found for {tool_name}"
            )

        artifact = artifacts[0]

        # 5. Validate filesystem boundary
        dossier_root = (
            root / "world" / dossier_id
        ).resolve()

        pdf_path = (root / target).resolve()

        if (
            not dossier_root.is_relative_to(root)
            or not pdf_path.is_relative_to(dossier_root)
        ):
            raise ValueError("Document path escapes dossier root")

        # 6. Verify SHA-256 against manifest
        pdf_bytes = pdf_path.read_bytes()

        actual_hash = hashlib.sha256(
            pdf_bytes
        ).hexdigest()

        if actual_hash != artifact["sha256"]:
            raise ValueError(
                "Document SHA-256 does not match manifest"
            )

        # 7. Extract PDF text
        with pymupdf.open(
            stream=pdf_bytes,
            filetype="pdf",
        ) as pdf:
            pages = [
                {
                    "page": page_number + 1,
                    "text": page.get_text(sort=True),
                }
                for page_number, page in enumerate(pdf)
            ]

        # 8. Return verified document and provenance
        return {
            "artifact_id": artifact["artifact_id"],
            "sha256": actual_hash,
            "source_class": artifact["source_class"],
            "locators": artifact["locators"],
            "pages": pages,
        }

    def get_inspection_report(
        self,
        dossier_id: str,
    ) -> dict:
        return self._read_report_pdf(
            dossier_id,
            "get_inspection_report",
        )

    def get_plumber_report(
        self,
        dossier_id: str,
    ) -> dict:
        return self._read_report_pdf(
            dossier_id,
            "get_plumber_report",
        )

    def get_endorsements(self, dossier_id: str) -> list[dict]:
        """Retrieve scheduled endorsement PDFs through contract and manifest."""

        if not re.fullmatch(r"d-[0-9a-f]{12}", dossier_id):
            raise ValueError("Invalid dossier ID")

        # 1. Read endorsement discovery rules from tool contract.
        contract_path = (
            Path(__file__).resolve().parent
            / "data_contract" / "tool_contracts.json"
        )
        contract = json.loads(
            contract_path.read_text(encoding="utf-8")
        )

        tools = [
            tool for tool in contract["tools"]
            if tool.get("name") == "get_endorsements"
            and tool.get("operation") == "LIST_ARTIFACTS"
        ]

        if len(tools) != 1:
            raise ValueError(
                "Missing or duplicate endorsement tool contract"
            )

        relative_pattern = tools[0].get("relative_pattern")

        if not isinstance(relative_pattern, str):
            raise ValueError("Invalid endorsement path pattern")

        pattern = PurePosixPath(relative_pattern)

        if (
            pattern.is_absolute()
            or "\\" in relative_pattern
            or ".." in pattern.parts
            or len(pattern.parts) != 2
            or pattern.parts[0] != "policy"
            or pattern.suffix.lower() != ".pdf"
        ):
            raise ValueError("Unauthorized endorsement path pattern")

        # 2. Load the dossier manifest and policy schedule.
        root = self.runtime_root.resolve()

        manifest = json.loads(
            (root / "manifests" / f"{dossier_id}.json")
            .read_text(encoding="utf-8")
        )

        policy_record = self.get_policy_record(dossier_id)
        schedule = policy_record.get("endorsement_schedule")

        if not isinstance(schedule, list):
            raise ValueError("Invalid endorsement schedule")

        # 3. Discover documents using the contract-defined pattern.
        dossier_prefix = f"world/{dossier_id}/"

        matched = [
            artifact for artifact in manifest["artifacts"]
            if artifact.get("media_type") == "application/pdf"
            and isinstance(artifact.get("path"), str)
            and artifact["path"].startswith(dossier_prefix)
            and fnmatchcase(
                artifact["path"][len(dossier_prefix):],
                relative_pattern,
            )
        ]

        by_id = {
            artifact["artifact_id"]: artifact
            for artifact in matched
        }

        if (
            len(matched) != len(by_id)
            or len(schedule) != len(matched)
        ):
            raise ValueError(
                "Endorsement schedule/manifest mismatch"
            )

        # 4. Verify each scheduled endorsement.
        policy_root = (
            root / "world" / dossier_id / "policy"
        ).resolve()

        documents = []
        seen = set()

        for scheduled in schedule:
            artifact_id = scheduled["artifact_id"]
            artifact = by_id.get(artifact_id)

            if (
                artifact is None
                or artifact_id in seen
                or scheduled["sha256"] != artifact["sha256"]
                or scheduled["policy_id"] != policy_record["policy_id"]
            ):
                raise ValueError(
                    "Invalid scheduled endorsement reference"
                )

            seen.add(artifact_id)

            pdf_path = (root / artifact["path"]).resolve()

            if not pdf_path.is_relative_to(policy_root):
                raise ValueError(
                    "Endorsement path escapes policy directory"
                )

            pdf_bytes = pdf_path.read_bytes()
            actual_hash = hashlib.sha256(pdf_bytes).hexdigest()

            if actual_hash != artifact["sha256"]:
                raise ValueError("Endorsement SHA-256 mismatch")

            # 5. Extract verified PDF text.
            with pymupdf.open(
                stream=pdf_bytes,
                filetype="pdf",
            ) as pdf:
                pages = [
                    {
                        "page": i + 1,
                        "text": page.get_text(sort=True),
                    }
                    for i, page in enumerate(pdf)
                ]

            documents.append({
                "form_id": scheduled["form_id"],
                "version": scheduled["version"],
                "effective": scheduled["effective"],
                "expires": scheduled["expires"],
                "artifact_id": artifact_id,
                "sha256": actual_hash,
                "source_class": artifact["source_class"],
                "locators": artifact["locators"],
                "pages": pages,
            })

        return documents
