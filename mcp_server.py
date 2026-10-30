from typing import Any

from mcp.server import MCPServer

from gateway import RuntimeGateway


gateway = RuntimeGateway()

mcp = MCPServer(
    "LossTrace Runtime"
)


@mcp.tool()
def get_claim_record(
    dossier_id: str,
) -> dict[str, Any]:
    """Get the claim record for one LossTrace dossier."""

    return gateway.get_claim_record(
        dossier_id
    )


@mcp.tool()
def get_policy_record(
    dossier_id: str,
) -> dict[str, Any]:
    """Get the policy record for one LossTrace dossier."""

    return gateway.get_policy_record(
        dossier_id
    )


@mcp.tool()
def get_ledger_record(
    dossier_id: str,
) -> dict[str, Any]:
    """Get the ledger record for one LossTrace dossier."""

    return gateway.get_ledger_record(
        dossier_id
    )



@mcp.tool()
def get_site_observations(
    dossier_id: str,
) -> dict[str, Any]:
    """Get structured site observations for a dossier."""

    return gateway.get_site_observations(dossier_id)


@mcp.tool()
def get_inspection_report(
    dossier_id: str,
) -> dict[str, Any]:
    """Read the site survey with source provenance."""

    return gateway.get_inspection_report(dossier_id)

@mcp.tool()
def get_plumber_report(
    dossier_id: str,
) -> dict[str, Any]:
    """Retrieve the service report through the runtime gateway."""

    return gateway.get_plumber_report(dossier_id)


@mcp.tool()
def get_policy_terms(
    dossier_id: str,
) -> dict[str, Any]:
    """Retrieve structured policy terms."""

    return gateway.get_policy_record(dossier_id)


@mcp.tool()
def get_policy_wording(
    dossier_id: str,
) -> dict[str, Any]:
    """Retrieve manifest-verified policy wording PDF."""

    return gateway.get_policy_wording(dossier_id)

@mcp.tool()
def get_endorsements(
    dossier_id: str,
) -> list[dict[str, Any]]:
    """Retrieve scheduled, verified policy endorsements."""

    return gateway.get_endorsements(dossier_id)

if __name__ == "__main__":
    mcp.run(
        transport="stdio"
    )