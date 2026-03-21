"""
labinator.ui — Interactive wizard helpers: prompts, back-navigation, confirmation.
"""

import random
import sys
import termios
import time
import tty
from pathlib import Path

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

_ROOT = Path(__file__).parent.parent

SKULL = "[bold red]☠[/bold red]"

# Sentinel values returned by wizard step functions.
BACK = object()   # user pressed ESC → go to previous step
SKIP = object()   # step doesn't apply right now (e.g. prefix/gateway in DHCP mode)


def q(widget_fn, *args, d: dict | None = None, key: str | None = None,
      silent: bool = False, cast=str, **kwargs):
    """Ask a question, using deployment file value as default or skipping in silent mode."""
    val = cast(d[key]) if (d and key and key in d and d[key] is not None) else None
    if val is not None and silent:
        return val
    if val is not None:
        kwargs["default"] = val
    result = widget_fn(*args, **kwargs).ask()
    if result is None:
        sys.exit(0)
    return result


def pt_text(question: str, *, default: str = "", validate=None, instruction: str = "",
            d: dict | None = None, key: str | None = None,
            silent: bool = False, cast=str):
    """Text prompt with ESC-to-go-back support via prompt_toolkit.

    Returns the entered string, or BACK if ESC was pressed.

    Parameters mirror q(): d/key/silent handle deploy-file pre-fill and silent mode.
    validate: callable(str) -> True | error_message_str
    """
    from prompt_toolkit.shortcuts import PromptSession
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.validation import Validator, ValidationError
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.styles import Style

    # Deploy-file / silent integration (same logic as q())
    val = cast(d[key]) if (d and key and key in d and d[key] is not None) else None
    if val is not None and silent:
        console.print(f"  [dim]{question} (from deployment file): {val}[/dim]")
        return val
    effective_default = str(val) if val is not None else str(default)

    _back = [False]
    kb = KeyBindings()

    @kb.add("escape")
    def _esc(event):
        _back[0] = True
        event.app.exit(result=effective_default)  # result is ignored; _back flag is checked

    pt_validator = None
    if validate:
        class _V(Validator):
            def validate(self, doc):
                if _back[0]:
                    return  # skip validation on ESC exit
                r = validate(doc.text)
                if r is not True:
                    raise ValidationError(message=str(r), cursor_position=len(doc.text))
        pt_validator = _V()

    # Style to match questionary's look
    qstyle = Style.from_dict({
        "qmark":       "fg:ansicyan bold",
        "prompt":      "bold",
        "instruction": "fg:ansibrightblack italic",
    })
    parts: list = [("class:qmark", "? "), ("class:prompt", question)]
    if instruction:
        parts += [("", " "), ("class:instruction", f"({instruction})")]
    parts.append(("", " "))

    session = PromptSession(
        message=FormattedText(parts),
        key_bindings=kb,
        validator=pt_validator,
        validate_while_typing=False,
        style=qstyle,
    )
    # Reduce escape timeouts so ESC feels instant.
    # ttimeoutlen: how long to wait for more bytes after \x1b before flushing the vt100 parser.
    # timeoutlen: how long the key processor waits for a follow-up key in multi-key sequences
    #             (emacs mode has escape+b, escape+f, etc. which would otherwise add ~1s delay).
    session.app.ttimeoutlen = 0.05
    session.app.timeoutlen = 0.05

    result = session.prompt(default=effective_default)
    if _back[0]:
        return BACK
    return result


def select_nav(question: str, choices: list, default=None):
    """questionary.select() with a ← Go Back option prepended.

    Returns the selected value, or BACK if ← Go Back is chosen or ESC is pressed.
    choices: list of plain values or questionary.Choice objects.
    """
    nav_choices = [questionary.Choice(title="← Go Back", value=BACK)] + list(choices)
    result = questionary.select(question, choices=nav_choices, default=default,
                                instruction="(arrow keys to move, Enter to select, ← Go Back to go back)").ask()
    if result is None or result is BACK:
        return BACK
    return result


def checkbox_nav(question: str, choices: list, defaults: list | None = None):
    """questionary.checkbox() with ESC-to-go-back support.

    Returns the selected list (possibly empty), or BACK if ESC is pressed.
    choices: list of plain strings or questionary.Choice objects.
    defaults: list of values to pre-check (optional).
    """
    from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings

    def _value(c):
        return c.value if isinstance(c, questionary.Choice) else c

    nav_choices = [
        questionary.Choice(
            title=c.title if isinstance(c, questionary.Choice) else str(c),
            value=_value(c),
            checked=(defaults is not None and _value(c) in defaults),
        )
        for c in choices
    ]

    qw = questionary.checkbox(
        question, choices=nav_choices,
        instruction="(space to select/deselect, Enter to confirm, ESC to go back)",
    )

    # Add ESC key binding to the underlying prompt_toolkit Application.
    kb = KeyBindings()

    @kb.add("escape")
    def _esc(event):
        event.app.exit(result=None)

    qw.application.key_bindings = merge_key_bindings([
        qw.application.key_bindings or KeyBindings(),
        kb,
    ])
    qw.application.ttimeoutlen = 0.05
    qw.application.timeoutlen  = 0.05

    result = qw.ask()
    if result is None:
        return BACK
    return result


def flush_stdin() -> None:
    """Discard any buffered keystrokes so accidental presses don't auto-confirm."""
    try:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        sys.stdin.flush()
        termios.tcflush(fd, termios.TCIFLUSH)
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        pass  # Not a TTY — skip (e.g. piped input)


def random_caps(word: str) -> str:
    """Return word with randomly mixed case — guaranteed at least one upper and one lower."""
    chars = [c.upper() if random.random() > 0.5 else c.lower() for c in word]
    if not any(c.isupper() for c in chars):
        idx = random.randrange(len(chars))
        chars[idx] = chars[idx].upper()
    if not any(c.islower() for c in chars):
        idx = random.randrange(len(chars))
        chars[idx] = chars[idx].lower()
    return "".join(chars)


def confirm_destruction(deploy: dict, kind: str = "VM") -> bool:
    """Display scary warning and require typed confirmation.

    kind: human-readable resource type shown in the warning panel ("VM" or "container").
    """
    hostname = deploy["hostname"]
    vmid = deploy.get("vmid", "???")
    node = deploy.get("node", "???")
    ip = deploy.get("ip_address") or deploy.get("ip", "???")

    challenge = random_caps("yes")

    console.print()
    console.print(Panel(
        Text.from_markup(
            f"{SKULL}  [bold red blink]WARNING: IRREVERSIBLE DESTRUCTION[/bold red blink]  {SKULL}\n\n"
            f"You are about to [bold red]PERMANENTLY DELETE[/bold red]:\n\n"
            f"  [bold]Hostname :[/bold] {hostname}\n"
            f"  [bold]VMID     :[/bold] {vmid}  (on {node})\n"
            f"  [bold]IP       :[/bold] {ip}\n\n"
            f"This will [bold red]STOP and DESTROY[/bold red] the {kind},\n"
            f"[bold red]REMOVE[/bold red] its DNS records, and\n"
            f"[bold red]DELETE[/bold red] it from the Ansible inventory.\n\n"
            f"[bold yellow]There is NO undo.[/bold yellow]"
        ),
        border_style="bold red",
        title=f"[bold red]{SKULL}  DECOMMISSION WIZARD  {SKULL}[/bold red]",
        padding=(1, 2),
    ))
    console.print()

    console.print("[yellow]Flushing keyboard buffer — please wait 5 seconds...[/yellow]")
    flush_stdin()
    for i in range(5, 0, -1):
        console.print(f"  [dim]{i}...[/dim]", end="\r")
        time.sleep(1)
    console.print()
    flush_stdin()  # discard any keystrokes made during the countdown

    console.print(
        f"[bold]To confirm destruction of [red]{hostname}[/red], "
        f"type exactly:[/bold] [bold yellow]{challenge}[/bold yellow]"
    )
    console.print("[dim](case-sensitive)[/dim]")
    console.print()

    try:
        answer = input("Type here: ").strip()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Aborted.[/yellow]")
        return False

    if answer != challenge:
        console.print(f"\n[red]✗ Expected '{challenge}', got '{answer}'. Aborted.[/red]")
        return False

    console.print("\n[bold red]Confirmed. Proceeding with decommission...[/bold red]")
    console.print()
    return True


def run_wizard_steps(steps: list, initial_state: dict | None = None) -> dict:
    """Run wizard step functions sequentially with ESC-to-go-back support.

    Each step is callable(state: dict) that returns one of:
      - Updated state dict  — step succeeded; advance to next step.
      - BACK               — user pressed ESC; return to previous step.
      - SKIP               — step doesn't apply right now; skip silently.

    ESC at the very first step prints "Aborted." and exits cleanly.
    Ctrl+C propagates naturally and exits the process immediately.
    SKIP steps are not pushed onto the history stack, so going back from a later
    step bypasses them correctly (e.g. prefix/gateway steps in DHCP mode).
    """
    state = dict(initial_state or {})
    history: list[int] = []
    i = 0
    while i < len(steps):
        result = steps[i](state)
        if result is BACK:
            if not history:
                console.print("\n[yellow]Aborted.[/yellow]")
                sys.exit(0)
            i = history.pop()
        elif result is SKIP:
            i += 1
        else:
            history.append(i)
            state = result
            i += 1
    return state


def prompt_package_profile(cfg: dict, deploy: dict, silent: bool,
                           nav: bool = False, current=None) -> tuple[str, list, list]:
    """Interactive package profile selection.

    Returns (package_profile, profile_packages, profile_tags).
    When nav=True, prepends ← Go Back and returns BACK on ESC instead of sys.exit.
    current: wizard state value — if provided, overrides deploy-file default.
    """
    from modules.profiles import resolve_profile
    profiles = cfg.get("package_profiles", {})
    deploy_profile = (deploy.get("package_profile", "") or "") if deploy else ""
    effective_profile = current if current is not None else deploy_profile
    if silent:
        package_profile = deploy_profile
        if package_profile and package_profile not in profiles:
            console.print(
                f"[yellow]Warning: package_profile '{package_profile}' not found in config "
                f"— skipping.[/yellow]"
            )
            package_profile = ""
        profile_packages, profile_tags = resolve_profile(package_profile, profiles)
    elif profiles:
        profile_choices = (
            [questionary.Choice(title="← Go Back", value=BACK)] if nav else []
        ) + [questionary.Choice(title="[none]", value="")] + [
            questionary.Choice(title=name, value=name) for name in profiles
        ]
        package_profile = questionary.select(
            "Package profile (optional):",
            choices=profile_choices,
            default=effective_profile if effective_profile in profiles else "",
            instruction="(arrow keys to move, Enter to select, ← Go Back to go back)" if nav else None,
        ).ask()
        if package_profile is None or package_profile is BACK:
            return BACK if nav else sys.exit(0)
        profile_packages, profile_tags = resolve_profile(package_profile, profiles)
    else:
        package_profile = ""
        profile_packages = []
        profile_tags = []
    return package_profile, profile_packages, profile_tags


def prompt_extra_packages(deploy: dict, silent: bool, nav: bool = False,
                          current=None) -> list[str]:
    """Interactive extra packages prompt. Returns list of package names.

    When nav=True, uses pt_text() so ESC returns BACK instead of sys.exit.
    current: wizard state value (list) — if provided, overrides deploy-file default.
    """
    deploy_extra_pkgs = deploy.get("extra_packages", []) if deploy else []
    effective_extra_pkgs = current if current is not None else deploy_extra_pkgs
    if silent:
        return deploy_extra_pkgs
    pkgs_default = ", ".join(effective_extra_pkgs) if effective_extra_pkgs else ""
    if nav:
        r = pt_text(
            "Extra packages to install (optional):",
            default=pkgs_default,
            instruction="comma-separated, e.g. htop, curl  —  leave blank for none",
        )
        if r is BACK:
            return BACK
        return [p.strip() for p in r.split(",") if p.strip()]
    pkgs_answer = questionary.text(
        "Extra packages to install (optional):",
        instruction="comma-separated, e.g. htop, curl  —  leave blank for none",
        default=pkgs_default,
    ).ask()
    if pkgs_answer is None:
        sys.exit(0)
    return [p.strip() for p in pkgs_answer.split(",") if p.strip()]


def prompt_node_selection(nodes: list[dict], deploy: dict, silent: bool,
                          memory_mb: int, memory_gb_str: str,
                          cpu_threshold: float, ram_threshold: float,
                          nav: bool = False) -> str:
    """Interactive node selection with resource filtering. Returns node name.

    When nav=True, prepends ← Go Back and returns BACK on ESC instead of sys.exit.
    """
    from modules.validation import node_passes_filter
    from modules.proxmox import smart_size
    filtered_nodes = [n for n in nodes if node_passes_filter(n, memory_mb, cpu_threshold, ram_threshold)]
    if not filtered_nodes:
        console.print(
            f"[yellow]Warning: No nodes pass the resource filter "
            f"(CPU <85%, RAM after +{memory_gb_str} GB <95%). Showing all nodes.[/yellow]"
        )
        filtered_nodes = nodes

    best_node = filtered_nodes[0]

    if silent:
        node_name = str(deploy.get("node", best_node["name"]))
        if not any(n["name"] == node_name for n in nodes):
            console.print(f"[red]ERROR: Node '{node_name}' from deployment file is not online.[/red]")
            sys.exit(1)
        console.print(f"  [dim]Node (from deployment file): {node_name}[/dim]")
        return node_name

    deploy_node = str(deploy.get("node", ""))
    max_name_len   = max(len(n["name"]) for n in filtered_nodes)
    max_ram_len    = max(len(f"{smart_size(n['free_mem'])} free / {smart_size(n['maxmem'])}") for n in filtered_nodes)
    max_shared_len = max(len(smart_size(n['shared_disk'])) for n in filtered_nodes)
    max_local_len  = max(len(smart_size(n['local_disk']))  for n in filtered_nodes)
    max_cpu_len    = max(len(f"{n['cpu'] * 100:.0f}%")     for n in filtered_nodes)
    node_choices = (
        [questionary.Choice(title="← Go Back", value=BACK)] if nav else []
    )
    for n in filtered_nodes:
        is_best = n["name"] == best_node["name"]
        suffix = "  [deploy file]" if n["name"] == deploy_node else ""
        ram_str    = f"{smart_size(n['free_mem'])} free / {smart_size(n['maxmem'])}".ljust(max_ram_len)
        shared_str = smart_size(n['shared_disk']).ljust(max_shared_len)
        local_str  = smart_size(n['local_disk']).ljust(max_local_len)
        cpu_str    = f"{n['cpu'] * 100:.0f}%".ljust(max_cpu_len)
        node_choices.append(questionary.Choice(
            title=[
                ("", "★ " if is_best else "  "),
                ("bold", n["name"].ljust(max_name_len)),
                ("", "  —  "),
                ("bold", "RAM:"),
                ("", f" [{ram_str}]  "),
                ("bold", "Disk:"),
                ("", f" [{local_str} (local) - {shared_str} (shared)]  "),
                ("bold", "CPU:"),
                ("", f" [{cpu_str}]{suffix}"),
            ],
            value=n["name"],
        ))
    default_node = (
        deploy_node if any(n["name"] == deploy_node for n in filtered_nodes)
        else best_node["name"]
    )
    hidden = len(nodes) - len(filtered_nodes)
    hint = f" ({hidden} node(s) hidden — over resource threshold)" if hidden else ""
    node_name = questionary.select(
        f"Select Proxmox node (★ = most free RAM){hint}:",
        choices=node_choices,
        default=default_node,
        instruction="(arrow keys to move, Enter to select, ← Go Back to go back)" if nav else None,
    ).ask()
    if node_name is None or node_name is BACK:
        return BACK if nav else sys.exit(0)
    return node_name


def make_common_wizard_steps(
    cfg: dict,
    deploy: dict,
    silent: bool,
    nodes: list,
    cpu_threshold: float,
    ram_threshold: float,
    hostname_label: str = "container",
) -> dict:
    """Return a dict of wizard step closures shared between deploy_lxc.py and deploy_vm.py.

    Keys: 'hostname', 'cpus', 'memory', 'disk', 'vlan', 'password',
          'package_profile', 'extra_packages', 'node'.

    Each value is a step closure compatible with run_wizard_steps().
    hostname_label: word used in the hostname prompt ("container" or "VM").
    """
    defaults = cfg["defaults"]
    addusername = defaults.get("addusername", "admin")

    def step_hostname(s):
        r = pt_text(
            f"Hostname for the new {hostname_label}:",
            default=s.get("hostname", ""),
            instruction="short name only — domain suffix appended in inventory",
            validate=lambda v: True if v.strip() else "Hostname cannot be empty",
            d=deploy, key="hostname", silent=silent,
        )
        if r is BACK:
            return BACK
        return {**s, "hostname": r.strip().lower()}

    def step_cpus(s):
        r = pt_text(
            "Number of vCPUs:",
            default=s.get("cpus_str", str(defaults.get("cpus", 2))),
            validate=lambda v: True if v.isdigit() and int(v) > 0 else "Must be a positive integer",
            d=deploy, key="cpus", silent=silent,
        )
        if r is BACK:
            return BACK
        return {**s, "cpus_str": r}

    def step_memory(s):
        r = pt_text(
            "Memory (GB):",
            default=s.get("memory_gb_str", str(defaults.get("memory_gb", 4))),
            validate=lambda v: (True if v.replace(".", "", 1).isdigit() and float(v) > 0
                                else "Must be a positive number"),
            d=deploy, key="memory_gb", silent=silent,
        )
        if r is BACK:
            return BACK
        return {**s, "memory_gb_str": r}

    def step_disk(s):
        r = pt_text(
            "Disk size (GB):",
            default=s.get("disk_gb_str", str(defaults.get("disk_gb", 100))),
            validate=lambda v: True if v.isdigit() and int(v) > 0 else "Must be a positive integer",
            d=deploy, key="disk_gb", silent=silent,
        )
        if r is BACK:
            return BACK
        return {**s, "disk_gb_str": r}

    def step_vlan(s):
        r = pt_text(
            "VLAN tag (bridge: vmbr0.<vlan>):",
            default=s.get("vlan_str", str(defaults.get("vlan", 220))),
            validate=lambda v: (True if v.isdigit() and 1 <= int(v) <= 4094
                                else "Must be a valid VLAN ID (1–4094)"),
            d=deploy, key="vlan", silent=silent,
        )
        if r is BACK:
            return BACK
        return {**s, "vlan_str": r}

    def step_password(s):
        r = pt_text(
            f"Root / {addusername} user password:",
            default=s.get("password", defaults.get("root_password", "changeme")),
            d=deploy, key="password", silent=silent,
        )
        if r is BACK:
            return BACK
        return {**s, "password": r}

    def step_package_profile(s):
        r = prompt_package_profile(cfg, deploy, silent, nav=True,
                                   current=s.get("package_profile"))
        if r is BACK:
            return BACK
        package_profile, profile_packages, profile_tags = r
        return {**s, "package_profile": package_profile,
                "profile_packages": profile_packages, "profile_tags": profile_tags}

    def step_extra_packages(s):
        r = prompt_extra_packages(deploy, silent, nav=True,
                                  current=s.get("extra_packages"))
        if r is BACK:
            return BACK
        return {**s, "extra_packages": r}

    def step_node(s):
        memory_mb = int(float(s["memory_gb_str"]) * 1024)
        r = prompt_node_selection(nodes, deploy, silent, memory_mb, s["memory_gb_str"],
                                  cpu_threshold, ram_threshold, nav=True)
        if r is BACK:
            return BACK
        return {**s, "node_name": r}

    return {
        "hostname":        step_hostname,
        "cpus":            step_cpus,
        "memory":          step_memory,
        "disk":            step_disk,
        "vlan":            step_vlan,
        "password":        step_password,
        "package_profile": step_package_profile,
        "extra_packages":  step_extra_packages,
        "node":            step_node,
    }
