"""Interactive mail prep."""

from __future__ import annotations


def prepare_mail(
    changespec_name: str,
    project_basename: str,
    target_dir: str,
    console: object | None,
) -> object | None:
    """Git-specific mail preparation: display branch info and confirm push."""
    from rich.console import Console as RichConsole
    from rich.markup import escape as escape_markup
    from rich.panel import Panel

    from sase.ace.mail_ops import MailPrepResult, get_cl_description
    from sase.vcs_provider import get_vcs_provider

    if not isinstance(console, RichConsole):
        return None

    provider = get_vcs_provider(target_dir)

    # Display current branch name
    branch_ok, branch_name = provider.get_branch_name(target_dir)
    if branch_ok and branch_name:
        console.print(f"\n[cyan]Branch: {branch_name}[/cyan]")

    # Display current description
    success, current_desc = get_cl_description(
        changespec_name,
        target_dir,
        console,
        project_basename=project_basename,
    )
    if success and current_desc:
        console.print(
            Panel(
                escape_markup(current_desc.rstrip()),
                title="Commit Description",
                border_style="cyan",
                padding=(1, 2),
            )
        )

    # Prompt user before pushing
    console.print(
        "\n[cyan]Do you want to push and create/update the PR now? (y/n):[/cyan] ",
        end="",
    )
    try:
        mail_response = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Aborted[/yellow]")
        return None

    should_mail = mail_response in ["y", "yes"]
    if not should_mail:
        console.print("[yellow]User declined to push[/yellow]")

    return MailPrepResult(should_mail=should_mail)
