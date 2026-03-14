"""GitHub configuration helpers."""

from sase.config import load_merged_config


def get_github_orgs() -> list[str]:
    """Read ``github_orgs`` from the merged sase config.

    Returns:
        A list of GitHub org/user names the user has push access to.
    """
    config = load_merged_config()
    value = config.get("github_orgs")
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if value:
        return [str(value)]
    return []
