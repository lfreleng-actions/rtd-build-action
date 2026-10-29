# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Tests for the Read the Docs build action.

Exercises name derivation, the verify lane and the merge lane against a
fake client, so the suite needs no network access and no Read the Docs
credentials.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import final
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.rtd_build import (  # noqa: E402 - path set above
    MODE_MERGE,
    MODE_VERIFY,
    Settings,
    run,
)
from lib.rtd_client import ReadTheDocsError  # noqa: E402 - path set above
from lib.rtd_naming import (  # noqa: E402 - path set above
    NamingError,
    parse_overrides,
    project_slug,
    project_slugify,
    repository_url_from,
    repository_url_from_change,
    repository_url_from_server,
    slugify,
    umbrella_from,
    umbrella_from_url,
)

ONAP_URL = "https://gerrit.onap.org/r/c/cps/+/141234"
ORAN_URL = "https://gerrit.o-ran-sc.org/r/c/ric-plt/+/9999"
ONAP_SERVER = "https://gerrit.onap.org/r"

#: Superseding builds the merge lane follows before giving up.
MAX_FOLLOWS = 10


@final
class FakeClient:
    """A Read the Docs client that records calls and answers from memory."""

    def __init__(
        self,
        projects: set[str] | None = None,
        subprojects: dict[str, list[str]] | None = None,
        versions: dict[tuple[str, str], bool] | None = None,
        default_versions: dict[str, str] | None = None,
        build_succeeds: bool = True,
        build_script: dict[str, list[dict[str, object]]] | None = None,
        running: list[dict[str, object]] | None = None,
    ) -> None:
        self.projects: set[str] = set(projects) if projects else set()
        self.subprojects: dict[str, list[str]] = dict(subprojects or {})
        self.versions: dict[tuple[str, str], bool] = dict(versions or {})
        self.default_versions: dict[str, str] = dict(default_versions or {})
        self.build_succeeds: bool = build_succeeds
        # Successive build_details answers per build; the last repeats.
        self.build_script: dict[str, list[dict[str, object]]] = dict(build_script or {})
        self.running: list[dict[str, object]] = list(running or [])
        # Successive build_list answers; once spent, running applies.
        self.running_answers: list[list[dict[str, object]]] = []
        self.calls: list[str] = []
        self.trigger_deadlines: list[float | None] = []
        self.builds: list[tuple[str, str]] = []
        self.repositories: dict[str, str] = {}
        self._next_build: int = 1000

    def project_exists(self, project: str) -> bool:
        """Report whether a project exists."""
        self.calls.append(f"exists:{project}")
        return project in self.projects

    def project_create(
        self, name: str, repository_url: str, homepage: str
    ) -> dict[str, object]:
        """Record a project creation."""
        self.calls.append(f"create:{name}")
        self.projects.add(name)
        self.repositories[name] = repository_url
        return {"name": name, "repository": repository_url, "homepage": homepage}

    def project_update(self, project: str, **fields: str) -> dict[str, object]:
        """Record a project update."""
        self.calls.append(f"update:{project}:{sorted(fields.items())}")
        if "default_version" in fields:
            self.default_versions[project] = fields["default_version"]
        return {"status": "success"}

    def default_version(self, project: str) -> str:
        """Return the recorded landing version."""
        return self.default_versions.get(project, "latest")

    def version_active(self, project: str, version: str) -> bool | None:
        """Report a version's active flag, or None when unknown."""
        self.calls.append(f"version:{project}/{version}")
        return self.versions.get((project, version))

    def version_activate(self, project: str, version: str) -> dict[str, object]:
        """Record a version activation."""
        self.calls.append(f"activate:{project}/{version}")
        self.versions[(project, version)] = True
        return {"status": "success"}

    def build_trigger(
        self, project: str, version: str, deadline: float | None = None
    ) -> str:
        """Record a build and return its identifier."""
        self.calls.append(f"build:{project}/{version}")
        self.builds.append((project, version))
        self.trigger_deadlines.append(deadline)
        self._next_build += 1
        # A triggered build makes Read the Docs aware of the version.
        _ = self.versions.setdefault((project, version), False)
        return str(self._next_build)

    def build_details(self, project: str, build_id: str) -> dict[str, object]:
        """Return the scripted state, else a finished build."""
        self.calls.append(f"details:{project}/{build_id}")
        script = self.build_script.get(build_id)
        if script:
            return script.pop(0) if len(script) > 1 else script[0]
        if self.build_succeeds:
            return {"id": build_id, "success": True, "state": {"code": "finished"}}
        return {"id": build_id, "success": False, "state": {"code": "finished"}}

    def build_list(self, project: str) -> list[dict[str, object]]:
        """List the builds Read the Docs has not yet finished."""
        self.calls.append(f"running:{project}")
        if self.running_answers:
            return self.running_answers.pop(0)
        return list(self.running)

    def subproject_list(self, project: str) -> list[str]:
        """List the recorded subprojects of a project."""
        self.calls.append(f"subprojects:{project}")
        return self.subprojects.get(project, [])

    def subproject_create(self, project: str, subproject: str) -> dict[str, object]:
        """Record a subproject relationship."""
        self.calls.append(f"attach:{project}/{subproject}")
        self.subprojects.setdefault(project, []).append(subproject)
        return {"status": "success"}


def fast(
    *,
    mode: str = MODE_VERIFY,
    gerrit_project: str = "cps",
    gerrit_url: str = "",
    gerrit_change_url: str = ONAP_URL,
    branch: str = "master",
    default_branch: str = "",
    default_version: str = "latest",
    repository_url: str = "",
    project: str = "",
    parent_project: str = "",
    project_overrides: str = "",
) -> Settings:
    """Build settings that never sleep between polls."""
    return Settings(
        mode=mode,
        gerrit_project=gerrit_project,
        gerrit_url=gerrit_url,
        gerrit_change_url=gerrit_change_url,
        branch=branch,
        default_branch=default_branch,
        default_version=default_version,
        repository_url=repository_url,
        project=project,
        parent_project=parent_project,
        project_overrides=project_overrides,
        poll_interval=0,
        build_timeout=5,
        create_timeout=5,
    )


class Slugify(unittest.TestCase):
    """Branch names convert to the VERSION slug Read the Docs stores."""

    def test_converts_slashes(self) -> None:
        self.assertEqual(slugify("maintenance/3.7.10"), "maintenance-3.7.10")
        self.assertEqual(slugify("mr/879/126960/2"), "mr-879-126960-2")

    def test_lowercases(self) -> None:
        self.assertEqual(slugify("Montreal"), "montreal")

    def test_preserves_dots_and_underscores(self) -> None:
        self.assertEqual(slugify("release/1.0_rc1"), "release-1.0_rc1")

    def test_rejects_empty(self) -> None:
        for value in ("", "   ", "///"):
            with self.assertRaises(NamingError):
                _ = slugify(value)


class ProjectSlugify(unittest.TestCase):
    """Project names follow stricter rules than versions.

    A Read the Docs project slug is a Django slug field, so it admits no
    dot. Reusing the version slugifier produced 'onap-.github' for the
    Gerrit project '.github', which the API rejected with HTTP 400.
    """

    def test_drops_dots(self) -> None:
        self.assertEqual(project_slugify("foo.bar"), "foo-bar")

    def test_collapses_hyphen_runs_left_by_a_separator(self) -> None:
        """'.github' prefixed by an umbrella yields a doubled hyphen."""
        self.assertEqual(project_slugify("onap-.github"), "onap-github")

    def test_trims_leading_and_trailing_hyphens(self) -> None:
        self.assertEqual(project_slugify(".github"), "github")

    def test_keeps_underscores_and_hyphens(self) -> None:
        """Both are valid in a Django slug, so neither gets rewritten."""
        self.assertEqual(project_slugify("a_b-c"), "a_b-c")

    def test_lowercases(self) -> None:
        self.assertEqual(project_slugify("Montreal"), "montreal")

    def test_rejects_empty(self) -> None:
        for value in ("", "   ", "..."):
            with self.assertRaises(NamingError):
                _ = project_slugify(value)

    def test_versions_keep_the_dot_projects_do_not(self) -> None:
        """The two slugifiers diverge, and each is right for its use."""
        self.assertEqual(slugify("maintenance/3.7.10"), "maintenance-3.7.10")
        self.assertEqual(project_slugify("maintenance/3.7.10"), "maintenance-3-7-10")


class ProjectSlugForGerritProjects(unittest.TestCase):
    """Derived project slugs, including the names already in service."""

    def test_derives_the_dotted_project(self) -> None:
        """The case that failed the ONAP merge lane."""
        self.assertEqual(project_slug("onap", ".github"), "onap-github")

    def test_leaves_established_names_untouched(self) -> None:
        """Changing a derivation must not rename a live project."""
        for project, expected in (
            ("cps", "onap-cps"),
            ("cps/ncmp-dmi-plugin", "onap-cps-ncmp-dmi-plugin"),
            ("aai/aai-common", "onap-aai-aai-common"),
            ("integration/csit", "onap-integration-csit"),
            ("oom", "onap-oom"),
        ):
            with self.subTest(project=project):
                self.assertEqual(project_slug("onap", project), expected)

    def test_rejects_an_empty_project(self) -> None:
        with self.assertRaises(NamingError):
            _ = project_slug("onap", "  ")


class UmbrellaDerivation(unittest.TestCase):
    """The umbrella comes from the Gerrit host."""

    def test_derives_from_change_urls(self) -> None:
        self.assertEqual(umbrella_from_url(ONAP_URL), "onap")
        self.assertEqual(umbrella_from_url(ORAN_URL), "o-ran-sc")
        self.assertEqual(
            umbrella_from_url("https://git.opendaylight.org/gerrit/x"), "opendaylight"
        )

    def test_derives_from_server_urls(self) -> None:
        """The server URL carries the same host as a change URL."""
        self.assertEqual(umbrella_from_url(ONAP_SERVER), "onap")
        self.assertEqual(umbrella_from_url("https://gerrit.o-ran-sc.org/r"), "o-ran-sc")

    def test_tolerates_a_bare_host(self) -> None:
        self.assertEqual(umbrella_from_url("gerrit.onap.org"), "onap")

    def test_rejects_unusable_values(self) -> None:
        for value in ("", "   ", "localhost"):
            with self.assertRaises(NamingError):
                _ = umbrella_from_url(value)

    def test_prefers_the_server_url(self) -> None:
        """Where both arrive, the server URL wins."""
        self.assertEqual(
            umbrella_from("https://gerrit.example.org/r", ONAP_URL), "example"
        )

    def test_falls_back_to_the_change_url(self) -> None:
        self.assertEqual(umbrella_from("", ONAP_URL), "onap")

    def test_rejects_two_empty_sources(self) -> None:
        with self.assertRaises(NamingError):
            _ = umbrella_from("", "")

    def test_project_slug_joins_nested_paths(self) -> None:
        self.assertEqual(
            project_slug("onap", "cps/ncmp-dmi-plugin"), "onap-cps-ncmp-dmi-plugin"
        )


class Overrides(unittest.TestCase):
    """Explicit rewrites provide an escape hatch."""

    def test_parses_pairs(self) -> None:
        self.assertEqual(parse_overrides("a=b\nc=d"), {"a": "b", "c": "d"})

    def test_accepts_commas(self) -> None:
        self.assertEqual(parse_overrides("a=b,c=d"), {"a": "b", "c": "d"})

    def test_rejects_malformed(self) -> None:
        with self.assertRaises(NamingError):
            _ = parse_overrides("notapair")


class NameResolution(unittest.TestCase):
    """Derivation runs by default, with overrides taking precedence."""

    def test_derives_project_and_parent(self) -> None:
        client = FakeClient(projects={"onap-cps", "onap-doc"})
        outcome = run(fast(), client)
        self.assertEqual(outcome.project, "onap-cps")
        self.assertEqual(outcome.parent_project, "onap-doc")

    def test_falls_back_to_bare_umbrella_parent(self) -> None:
        """An imported docs estate holds the umbrella under its bare name.

        This reproduces the ONAP case without naming ONAP: 'onap-doc'
        does not exist, but 'onap' does.
        """
        client = FakeClient(projects={"onap-cps", "onap"})
        outcome = run(fast(), client)
        self.assertEqual(outcome.parent_project, "onap")

    def test_umbrella_docs_repo_resolves_to_the_umbrella(self) -> None:
        """The 'doc' repository itself maps onto the umbrella project."""
        client = FakeClient(projects={"onap"})
        outcome = run(fast(gerrit_project="doc"), client)
        self.assertEqual(outcome.project, "onap")
        self.assertEqual(outcome.parent_project, "onap")

    def test_explicit_inputs_win(self) -> None:
        client = FakeClient(projects={"custom", "custom-parent"})
        settings = fast(project="custom", parent_project="custom-parent")
        outcome = run(settings, client)
        self.assertEqual(outcome.project, "custom")
        self.assertEqual(outcome.parent_project, "custom-parent")

    def test_overrides_rewrite_derived_names(self) -> None:
        client = FakeClient(projects={"onap-cps", "onap-doc", "renamed"})
        settings = fast(project_overrides="onap-cps=renamed")
        outcome = run(settings, client)
        self.assertEqual(outcome.project, "renamed")

    def test_derives_a_dotted_gerrit_project(self) -> None:
        """A project slug admits no dot; '.github' must not keep one."""
        client = FakeClient(projects={"onap-github", "onap-doc"})
        outcome = run(fast(gerrit_project=".github"), client)
        self.assertEqual(outcome.project, "onap-github")

    def test_a_dotted_override_source_matches(self) -> None:
        """An override keyed on a dotted name reaches the project.

        parse_overrides normalises both sides with the project rules, so
        resolve_names must look up with the same rules. Normalising the
        lookup with the version rules leaves the dot in place, the key
        never matches, and the override silently does nothing.

        An explicit input carries the dot as far as the lookup, which a
        derived name cannot: project_slug has already removed it.
        """
        client = FakeClient(projects={"renamed", "onap-doc"})
        settings = fast(
            project="onap-.github",
            parent_project="onap-doc",
            project_overrides="onap-.github=renamed",
        )
        outcome = run(settings, client)
        self.assertEqual(outcome.project, "renamed")

    def test_an_explicit_dotted_project_normalises(self) -> None:
        """An explicit input takes the project rules, not the version ones."""
        client = FakeClient(projects={"onap-github", "custom-parent"})
        settings = fast(project="onap-.github", parent_project="custom-parent")
        outcome = run(settings, client)
        self.assertEqual(outcome.project, "onap-github")

    def test_a_dotted_parent_suffix_matches_the_docs_repo(self) -> None:
        """The umbrella fallback compares with the project rules too."""
        client = FakeClient(projects={"onap"})
        outcome = run(fast(gerrit_project=".doc"), client)
        self.assertEqual(outcome.project, "onap")


class VerifyLane(unittest.TestCase):
    """Verify reports without changing anything."""

    def test_reports_an_existing_project(self) -> None:
        client = FakeClient(projects={"onap-cps", "onap-doc"})
        outcome = run(fast(mode=MODE_VERIFY), client)
        self.assertTrue(outcome.project_exists)
        self.assertFalse(outcome.project_created)
        self.assertEqual(outcome.build_id, "")

    def test_reports_a_missing_project_without_creating_it(self) -> None:
        client = FakeClient(projects={"onap-doc"})
        outcome = run(fast(mode=MODE_VERIFY), client)
        self.assertFalse(outcome.project_exists)
        self.assertFalse(outcome.project_created)
        self.assertNotIn("create:onap-cps", client.calls)

    def test_never_triggers_a_build(self) -> None:
        client = FakeClient(projects={"onap-cps", "onap-doc"})
        _ = run(fast(mode=MODE_VERIFY), client)
        self.assertEqual(client.builds, [])


class MergeLane(unittest.TestCase):
    """Merge publishes the documentation."""

    def test_builds_the_default_version_on_master(self) -> None:
        client = FakeClient(
            projects={"onap-cps", "onap-doc"}, subprojects={"onap-doc": ["onap-cps"]}
        )
        outcome = run(fast(mode=MODE_MERGE), client)
        self.assertTrue(outcome.build_success)
        self.assertEqual(client.builds, [("onap-cps", "latest")])

    def test_creates_a_missing_project(self) -> None:
        client = FakeClient(projects={"onap-doc"})
        outcome = run(fast(mode=MODE_MERGE), client)
        self.assertTrue(outcome.project_created)
        self.assertIn("create:onap-cps", client.calls)

    def test_attaches_a_new_subproject(self) -> None:
        client = FakeClient(projects={"onap-cps", "onap-doc"})
        outcome = run(fast(mode=MODE_MERGE), client)
        self.assertTrue(outcome.subproject_created)
        self.assertIn("attach:onap-doc/onap-cps", client.calls)

    def test_leaves_an_existing_subproject_alone(self) -> None:
        client = FakeClient(
            projects={"onap-cps", "onap-doc"}, subprojects={"onap-doc": ["onap-cps"]}
        )
        outcome = run(fast(mode=MODE_MERGE), client)
        self.assertFalse(outcome.subproject_created)

    def test_skips_the_relationship_when_project_is_its_own_umbrella(self) -> None:
        client = FakeClient(projects={"onap"})
        outcome = run(fast(mode=MODE_MERGE, gerrit_project="doc"), client)
        self.assertFalse(outcome.subproject_created)
        self.assertNotIn("attach:onap/onap", client.calls)

    def test_sets_the_landing_version_when_it_differs(self) -> None:
        client = FakeClient(
            projects={"onap-cps", "onap-doc"},
            subprojects={"onap-doc": ["onap-cps"]},
            default_versions={"onap-cps": "stable"},
        )
        outcome = run(fast(mode=MODE_MERGE), client)
        self.assertTrue(outcome.default_version_changed)

    def test_leaves_a_matching_landing_version_alone(self) -> None:
        client = FakeClient(
            projects={"onap-cps", "onap-doc"},
            subprojects={"onap-doc": ["onap-cps"]},
            default_versions={"onap-cps": "latest"},
        )
        outcome = run(fast(mode=MODE_MERGE), client)
        self.assertFalse(outcome.default_version_changed)

    def test_reports_a_failed_build(self) -> None:
        client = FakeClient(
            projects={"onap-cps", "onap-doc"},
            subprojects={"onap-doc": ["onap-cps"]},
            build_succeeds=False,
        )
        with self.assertRaises(ReadTheDocsError):
            _ = run(fast(mode=MODE_MERGE), client)


def build(
    build_id: int,
    code: str,
    success: bool | None = None,
    version: str = "latest",
) -> dict[str, object]:
    """Describe a build the way the Read the Docs v3 API does."""
    return {
        "id": build_id,
        "version": version,
        "state": {"code": code, "name": code.title()},
        "success": success,
        "urls": {
            "build": f"https://app.readthedocs.org/projects/onap-cps/builds/{build_id}/"
        },
    }


class SupersededBuilds(unittest.TestCase):
    """A cancelled build hands over to the build that replaced it.

    Read the Docs keeps one queue per version and cancels a queued or
    running build when another is triggered for the same version: a
    second merge landing moments later, or the repository webhook firing
    for the same merge. The newer build checks out the branch after our
    merge, so its result speaks for our change too. Reporting the
    cancellation as a failure turned every such overlap red.
    """

    def _client(
        self,
        script: dict[str, list[dict[str, object]]],
        running: list[dict[str, object]] | None = None,
    ) -> FakeClient:
        return FakeClient(
            projects={"onap-cps", "onap-doc"},
            subprojects={"onap-doc": ["onap-cps"]},
            build_script=script,
            running=running,
        )

    def test_follows_the_build_that_superseded_ours(self) -> None:
        client = self._client(
            {
                "1001": [build(1001, "cancelled", False)],
                "2000": [build(2000, "building"), build(2000, "finished", True)],
            },
            running=[build(2000, "building")],
        )
        outcome = run(fast(mode=MODE_MERGE), client)
        self.assertTrue(outcome.build_success)
        self.assertEqual(outcome.build_id, "2000")
        self.assertEqual(client.builds, [("onap-cps", "latest")])
        self.assertTrue(any("superseded" in note for note in outcome.notes))

    def test_follows_a_chain_of_supersessions(self) -> None:
        """A merge burst can supersede many builds; each contains the change.

        Following polls without triggering, so it never counts against
        the trigger limit.
        """
        chain = [str(n) for n in range(2000, 2005)]
        script = {"1001": [build(1001, "cancelled", False)]}
        script |= {n: [build(int(n), "cancelled", False)] for n in chain[:-1]}
        script[chain[-1]] = [build(int(chain[-1]), "finished", True)]
        client = self._client(script)
        client.running_answers = [[build(int(n), "building")] for n in chain]
        outcome = run(fast(mode=MODE_MERGE), client)
        self.assertTrue(outcome.build_success)
        self.assertEqual(outcome.build_id, chain[-1])
        self.assertEqual(len(client.builds), 1)

    def test_reports_a_failure_of_the_superseding_build(self) -> None:
        client = self._client(
            {
                "1001": [build(1001, "cancelled", False)],
                "2000": [build(2000, "finished", False)],
            },
            running=[build(2000, "building")],
        )
        with self.assertRaisesRegex(ReadTheDocsError, "2000"):
            _ = run(fast(mode=MODE_MERGE), client)

    def test_triggers_again_when_nothing_newer_is_running(self) -> None:
        client = self._client({"1001": [build(1001, "cancelled", False)]})
        outcome = run(fast(mode=MODE_MERGE), client)
        self.assertTrue(outcome.build_success)
        self.assertEqual(outcome.build_id, "1002")
        self.assertEqual(len(client.builds), 2)

    def test_ignores_running_builds_of_other_versions(self) -> None:
        client = self._client(
            {"1001": [build(1001, "cancelled", False)]},
            running=[build(2000, "building", version="maintenance-3.7.10")],
        )
        outcome = run(fast(mode=MODE_MERGE), client)
        self.assertEqual(outcome.build_id, "1002")
        self.assertNotIn("details:onap-cps/2000", client.calls)

    def test_ignores_running_builds_older_than_ours(self) -> None:
        """An older build predates our merge, so it cannot vouch for it."""
        client = self._client(
            {"1001": [build(1001, "cancelled", False)]},
            running=[build(900, "building")],
        )
        outcome = run(fast(mode=MODE_MERGE), client)
        self.assertEqual(outcome.build_id, "1002")
        self.assertNotIn("details:onap-cps/900", client.calls)

    def test_gives_up_after_repeated_cancellation(self) -> None:
        cancelled = [build(0, "cancelled", False)]
        client = self._client({str(n): list(cancelled) for n in range(1001, 1010)})
        with self.assertRaisesRegex(ReadTheDocsError, "cancelled"):
            _ = run(fast(mode=MODE_MERGE), client)
        self.assertEqual(len(client.builds), 3)

    def test_reads_the_state_object(self) -> None:
        """The v3 API reports state as an object, not a string."""
        client = self._client({"1001": [build(1001, "failed")]})
        with self.assertRaisesRegex(ReadTheDocsError, "1001.*failed"):
            _ = run(fast(mode=MODE_MERGE), client)

    def test_a_failure_links_the_build_page(self) -> None:
        client = self._client({"1001": [build(1001, "finished", False)]})
        with self.assertRaisesRegex(
            ReadTheDocsError, "app.readthedocs.org/projects/onap-cps/builds/1001/"
        ):
            _ = run(fast(mode=MODE_MERGE), client)

    def test_triggers_nothing_once_the_timeout_passes(self) -> None:
        """A build triggered after the deadline would go unmonitored."""
        client = self._client({"1001": [build(1001, "cancelled", False)]})
        clock = [0.0]

        def listed(_project: str) -> list[dict[str, object]]:
            clock[0] = 60.0  # the request returned after the build timeout
            return []

        with (
            mock.patch("time.monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(client, "build_list", side_effect=listed),
            self.assertRaisesRegex(ReadTheDocsError, "no time left"),
        ):
            _ = run(fast(mode=MODE_MERGE), client)
        self.assertEqual(len(client.builds), 1)

    def test_bounds_every_trigger_by_the_deadline(self) -> None:
        """API retries inside a trigger must not outlast the build timeout."""
        client = self._client({"1001": [build(1001, "cancelled", False)]})
        _ = run(fast(mode=MODE_MERGE), client)
        self.assertEqual(len(client.trigger_deadlines), 2)
        self.assertNotIn(None, client.trigger_deadlines)

    def _chain(self, follows: int, *, ends_in_success: bool) -> FakeClient:
        """Script a trigger superseded by a chain of newer builds.

        Each newer build is cancelled in turn; the last one succeeds
        when ``ends_in_success`` is set and is superseded too otherwise.
        """
        chain = [str(n) for n in range(2000, 2000 + follows)]
        script = {"1001": [build(1001, "cancelled", False)]}
        script |= {n: [build(int(n), "cancelled", False)] for n in chain}
        if ends_in_success:
            script[chain[-1]] = [build(int(chain[-1]), "finished", True)]
        client = self._client(script)
        client.running_answers = [[build(int(n), "building")] for n in chain]
        if not ends_in_success:
            client.running_answers.append([build(2000 + follows, "building")])
        return client

    @unittest.expectedFailure
    def test_stops_following_after_the_follow_limit(self) -> None:
        """An endless chain of supersessions must not poll until timeout."""
        client = self._chain(MAX_FOLLOWS, ends_in_success=False)
        last = str(2000 + MAX_FOLLOWS - 1)
        with self.assertRaisesRegex(
            ReadTheDocsError, rf"followed {MAX_FOLLOWS} .*{last}"
        ):
            _ = run(fast(mode=MODE_MERGE), client)
        self.assertEqual(len(client.builds), 1)

    @unittest.expectedFailure
    def test_follows_up_to_the_follow_limit(self) -> None:
        client = self._chain(MAX_FOLLOWS, ends_in_success=True)
        outcome = run(fast(mode=MODE_MERGE), client)
        self.assertTrue(outcome.build_success)
        self.assertEqual(outcome.build_id, str(2000 + MAX_FOLLOWS - 1))
        self.assertEqual(len(client.builds), 1)
        self.assertEqual(outcome.as_dict()["cancelled_builds"], MAX_FOLLOWS)


class CancelledBuildCount(unittest.TestCase):
    """The outcome reports every cancellation the merge lane observed."""

    def _client(self, script: dict[str, list[dict[str, object]]]) -> FakeClient:
        return FakeClient(
            projects={"onap-cps", "onap-doc"},
            subprojects={"onap-doc": ["onap-cps"]},
            build_script=script,
        )

    @unittest.expectedFailure
    def test_counts_a_followed_cancellation(self) -> None:
        client = self._client({"1001": [build(1001, "cancelled", False)]})
        client.running_answers = [[build(2000, "building")]]
        outcome = run(fast(mode=MODE_MERGE), client)
        self.assertEqual(outcome.build_id, "2000")
        self.assertEqual(outcome.as_dict()["cancelled_builds"], 1)

    @unittest.expectedFailure
    def test_counts_a_retriggered_cancellation(self) -> None:
        client = self._client({"1001": [build(1001, "cancelled", False)]})
        outcome = run(fast(mode=MODE_MERGE), client)
        self.assertEqual(outcome.build_id, "1002")
        self.assertEqual(outcome.as_dict()["cancelled_builds"], 1)

    @unittest.expectedFailure
    def test_counts_follows_and_retriggers_together(self) -> None:
        client = self._client(
            {
                "1001": [build(1001, "cancelled", False)],
                "2000": [build(2000, "cancelled", False)],
            }
        )
        client.running_answers = [[build(2000, "building")], []]
        outcome = run(fast(mode=MODE_MERGE), client)
        self.assertEqual(outcome.build_id, "1002")
        self.assertEqual(len(client.builds), 2)
        self.assertEqual(outcome.as_dict()["cancelled_builds"], 2)

    @unittest.expectedFailure
    def test_reports_zero_without_cancellation(self) -> None:
        outcome = run(fast(mode=MODE_MERGE), self._client({}))
        self.assertTrue(outcome.build_success)
        self.assertEqual(outcome.as_dict()["cancelled_builds"], 0)

    @unittest.expectedFailure
    def test_emits_the_count_as_an_output(self) -> None:
        from lib.emit_outputs import render  # noqa: PLC0415

        lines = render({"cancelled_builds": 2}, "{}")
        self.assertIn("cancelled_builds=2", lines)


class SlashedBranches(unittest.TestCase):
    """A branch containing a slash reaches the API as a slug.

    Passing the raw branch name produced a request path that did not
    resolve, which is the defect this action exists to prevent.
    """

    def test_slugifies_the_version(self) -> None:
        client = FakeClient(
            projects={"onap-cps", "onap-doc"},
            subprojects={"onap-doc": ["onap-cps"]},
            versions={("onap-cps", "maintenance-3.7.10"): True},
        )
        settings = fast(mode=MODE_MERGE, branch="maintenance/3.7.10")
        outcome = run(settings, client)
        self.assertEqual(outcome.version_slug, "maintenance-3.7.10")
        self.assertIn(("onap-cps", "maintenance-3.7.10"), client.builds)

    def test_never_sends_a_raw_branch_name(self) -> None:
        client = FakeClient(
            projects={"onap-cps", "onap-doc"},
            subprojects={"onap-doc": ["onap-cps"]},
            versions={("onap-cps", "maintenance-3.7.10"): True},
        )
        settings = fast(mode=MODE_MERGE, branch="maintenance/3.7.10")
        _ = run(settings, client)
        for project, version in client.builds:
            self.assertNotIn("/", version, f"{project} received an unslugified version")

    def test_discovers_an_unseen_branch(self) -> None:
        """An unknown branch builds the default version first."""
        client = FakeClient(
            projects={"onap-cps", "onap-doc"}, subprojects={"onap-doc": ["onap-cps"]}
        )
        settings = fast(mode=MODE_MERGE, branch="maintenance/3.7.10")
        outcome = run(settings, client)
        self.assertEqual(client.builds[0], ("onap-cps", "latest"))
        self.assertEqual(client.builds[1], ("onap-cps", "maintenance-3.7.10"))
        self.assertTrue(outcome.version_activated)

    def test_activates_a_discovered_branch(self) -> None:
        client = FakeClient(
            projects={"onap-cps", "onap-doc"},
            subprojects={"onap-doc": ["onap-cps"]},
            versions={("onap-cps", "maintenance-3.7.10"): False},
        )
        settings = fast(mode=MODE_MERGE, branch="maintenance/3.7.10")
        outcome = run(settings, client)
        self.assertTrue(outcome.version_activated)
        self.assertIn("activate:onap-cps/maintenance-3.7.10", client.calls)


class DefaultBranch(unittest.TestCase):
    """The repository's default branch publishes as the default version.

    Read the Docs tracks that branch as ``latest`` rather than creating a
    version slug for it, so asking for a version named ``master`` would
    address something that does not exist.
    """

    def _client(self) -> FakeClient:
        return FakeClient(
            projects={"onap-cps", "onap-doc"}, subprojects={"onap-doc": ["onap-cps"]}
        )

    def test_master_builds_the_default_version(self) -> None:
        client = self._client()
        outcome = run(fast(mode=MODE_MERGE, branch="master"), client)
        self.assertEqual(outcome.version_slug, "latest")
        self.assertEqual(client.builds, [("onap-cps", "latest")])

    def test_main_builds_the_default_version(self) -> None:
        client = self._client()
        outcome = run(fast(mode=MODE_MERGE, branch="main"), client)
        self.assertEqual(outcome.version_slug, "latest")
        self.assertEqual(client.builds, [("onap-cps", "latest")])

    def test_an_explicit_default_branch_wins(self) -> None:
        client = self._client()
        settings = fast(mode=MODE_MERGE, branch="trunk", default_branch="trunk")
        outcome = run(settings, client)
        self.assertEqual(outcome.version_slug, "latest")

    def test_naming_a_default_branch_demotes_master(self) -> None:
        """With 'trunk' named, 'master' becomes an ordinary branch."""
        client = FakeClient(
            projects={"onap-cps", "onap-doc"},
            subprojects={"onap-doc": ["onap-cps"]},
            versions={("onap-cps", "master"): True},
        )
        settings = fast(mode=MODE_MERGE, branch="master", default_branch="trunk")
        outcome = run(settings, client)
        self.assertEqual(outcome.version_slug, "master")


class OtherConsumers(unittest.TestCase):
    """Derivation serves organisations beyond ONAP."""

    def test_handles_a_hyphenated_umbrella(self) -> None:
        client = FakeClient(projects={"o-ran-sc-ric-plt", "o-ran-sc-doc"})
        settings = fast(gerrit_project="ric-plt", gerrit_change_url=ORAN_URL)
        outcome = run(settings, client)
        self.assertEqual(outcome.project, "o-ran-sc-ric-plt")
        self.assertEqual(outcome.parent_project, "o-ran-sc-doc")

    def test_handles_a_nested_gerrit_project(self) -> None:
        client = FakeClient(projects={"onap-cps-ncmp-dmi-plugin", "onap-doc"})
        outcome = run(fast(gerrit_project="cps/ncmp-dmi-plugin"), client)
        self.assertEqual(outcome.project, "onap-cps-ncmp-dmi-plugin")


class RepositoryUrl(unittest.TestCase):
    """A new project records a clonable URL, not a review URL."""

    def test_joins_the_server_url_and_project(self) -> None:
        """The server URL already addresses the repository root."""
        self.assertEqual(
            repository_url_from_server(ONAP_SERVER, "cps"),
            "https://gerrit.onap.org/r/cps",
        )

    def test_tolerates_a_trailing_slash(self) -> None:
        self.assertEqual(
            repository_url_from_server("https://gerrit.onap.org/r/", "cps"),
            "https://gerrit.onap.org/r/cps",
        )

    def test_derives_from_an_onap_change_url(self) -> None:
        self.assertEqual(
            repository_url_from_change(ONAP_URL, "cps"),
            "https://gerrit.onap.org/r/cps",
        )

    def test_derives_from_an_opendaylight_change_url(self) -> None:
        """OpenDaylight serves reviews under a different path prefix."""
        self.assertEqual(
            repository_url_from_change(
                "https://git.opendaylight.org/gerrit/c/docs/+/1", "docs"
            ),
            "https://git.opendaylight.org/gerrit/docs",
        )

    def test_keeps_a_nested_project_path(self) -> None:
        self.assertEqual(
            repository_url_from_change(ONAP_URL, "cps/ncmp-dmi-plugin"),
            "https://gerrit.onap.org/r/cps/ncmp-dmi-plugin",
        )

    def test_rejects_a_url_with_no_review_segment(self) -> None:
        with self.assertRaises(NamingError):
            _ = repository_url_from_change("https://gerrit.onap.org/", "cps")

    def test_prefers_the_server_url(self) -> None:
        """Joining the server URL needs no knowledge of review layout."""
        self.assertEqual(
            repository_url_from(ONAP_SERVER, ONAP_URL, "cps"),
            "https://gerrit.onap.org/r/cps",
        )

    def test_falls_back_to_the_change_url(self) -> None:
        self.assertEqual(
            repository_url_from("", ONAP_URL, "cps"),
            "https://gerrit.onap.org/r/cps",
        )

    def test_rejects_two_empty_sources(self) -> None:
        with self.assertRaises(NamingError):
            _ = repository_url_from("", "", "cps")

    def test_creation_records_the_repository_not_the_change(self) -> None:
        client = FakeClient(projects={"onap-doc"})
        _ = run(fast(mode=MODE_MERGE), client)
        created = [c for c in client.calls if c.startswith("create:")]
        self.assertEqual(created, ["create:onap-cps"])
        self.assertEqual(
            client.repositories["onap-cps"], "https://gerrit.onap.org/r/cps"
        )

    def test_creation_from_a_server_url_alone(self) -> None:
        """A caller following the org convention supplies gerrit_url only."""
        client = FakeClient(projects={"onap-doc"})
        settings = fast(mode=MODE_MERGE, gerrit_url=ONAP_SERVER, gerrit_change_url="")
        outcome = run(settings, client)
        self.assertEqual(outcome.project, "onap-cps")
        self.assertEqual(
            client.repositories["onap-cps"], "https://gerrit.onap.org/r/cps"
        )

    def test_an_explicit_repository_url_wins(self) -> None:
        client = FakeClient(projects={"onap-doc"})
        settings = fast(
            mode=MODE_MERGE, repository_url="https://example.org/mirror.git"
        )
        _ = run(settings, client)
        self.assertEqual(
            client.repositories["onap-cps"], "https://example.org/mirror.git"
        )


class LandingVersusLatest(unittest.TestCase):
    """The landing version and the default-branch alias stay separate.

    Read the Docs fixes the default branch's slug as ``latest``. A
    project may still point its landing page elsewhere, so building
    whatever ``default_version`` names would build the wrong thing.
    """

    def _client(self) -> FakeClient:
        return FakeClient(
            projects={"onap-cps", "onap-doc"},
            subprojects={"onap-doc": ["onap-cps"]},
            default_versions={"onap-cps": "latest"},
        )

    def test_default_branch_builds_latest_not_the_landing_version(self) -> None:
        client = self._client()
        settings = fast(mode=MODE_MERGE, branch="master", default_version="stable")
        outcome = run(settings, client)
        self.assertEqual(outcome.version_slug, "latest")
        self.assertIn(("onap-cps", "latest"), client.builds)
        self.assertNotIn(("onap-cps", "stable"), client.builds)

    def test_discovery_builds_latest_not_the_landing_version(self) -> None:
        client = self._client()
        settings = fast(
            mode=MODE_MERGE,
            branch="maintenance/3.7.10",
            default_version="stable",
        )
        _ = run(settings, client)
        self.assertEqual(client.builds[0], ("onap-cps", "latest"))
        self.assertNotIn(("onap-cps", "stable"), client.builds)

    def test_landing_version_still_reaches_the_project(self) -> None:
        client = self._client()
        settings = fast(mode=MODE_MERGE, branch="master", default_version="stable")
        outcome = run(settings, client)
        self.assertTrue(outcome.default_version_changed)
        self.assertEqual(client.default_versions["onap-cps"], "stable")


class Rendering(unittest.TestCase):
    """The summary renderer survives awkward text."""

    def test_notes_stay_on_one_line_each(self) -> None:
        """An API error can span lines; a list item cannot."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from lib.render_summary import note_lines  # noqa: PLC0415

        outcome: dict[str, object] = {
            "notes": ["Read the Docs said:\n  detail: Not found.\n  code: 404"]
        }
        for line in note_lines(outcome):
            self.assertNotIn("\n", line)

    def test_omits_the_cancellation_row_when_none_occurred(self) -> None:
        from lib.render_summary import property_rows  # noqa: PLC0415

        rows = property_rows({"build_id": "1001", "cancelled_builds": 0})
        self.assertFalse(any("Cancelled" in row for row in rows))

    @unittest.expectedFailure
    def test_shows_the_cancellation_row_when_some_occurred(self) -> None:
        from lib.render_summary import property_rows  # noqa: PLC0415

        rows = property_rows({"build_id": "2000", "cancelled_builds": 3})
        self.assertIn("| Cancelled builds | 3 |", rows)


if __name__ == "__main__":
    _ = unittest.main(verbosity=2)
