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
    repository_url_from,
    slugify,
    umbrella_from_url,
)

ONAP_URL = "https://gerrit.onap.org/r/c/cps/+/141234"
ORAN_URL = "https://gerrit.o-ran-sc.org/r/c/ric-plt/+/9999"


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
    ) -> None:
        self.projects: set[str] = set(projects) if projects else set()
        self.subprojects: dict[str, list[str]] = dict(subprojects or {})
        self.versions: dict[tuple[str, str], bool] = dict(versions or {})
        self.default_versions: dict[str, str] = dict(default_versions or {})
        self.build_succeeds: bool = build_succeeds
        self.calls: list[str] = []
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

    def build_trigger(self, project: str, version: str) -> str:
        """Record a build and return its identifier."""
        self.calls.append(f"build:{project}/{version}")
        self.builds.append((project, version))
        self._next_build += 1
        # A triggered build makes Read the Docs aware of the version.
        _ = self.versions.setdefault((project, version), False)
        return str(self._next_build)

    def build_details(self, project: str, build_id: str) -> dict[str, object]:
        """Return a finished build, successful or otherwise."""
        self.calls.append(f"details:{project}/{build_id}")
        if self.build_succeeds:
            return {"id": build_id, "success": True, "state": "finished"}
        return {"id": build_id, "success": False, "state": "failed"}

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
    """Branch names convert to the slug Read the Docs stores."""

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


class UmbrellaDerivation(unittest.TestCase):
    """The umbrella comes from the Gerrit host."""

    def test_derives_from_change_urls(self) -> None:
        self.assertEqual(umbrella_from_url(ONAP_URL), "onap")
        self.assertEqual(umbrella_from_url(ORAN_URL), "o-ran-sc")
        self.assertEqual(
            umbrella_from_url("https://git.opendaylight.org/gerrit/x"), "opendaylight"
        )

    def test_rejects_unusable_values(self) -> None:
        for value in ("", "   ", "localhost"):
            with self.assertRaises(NamingError):
                _ = umbrella_from_url(value)

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

    def test_derives_from_an_onap_change_url(self) -> None:
        self.assertEqual(
            repository_url_from(ONAP_URL, "cps"),
            "https://gerrit.onap.org/r/cps",
        )

    def test_derives_from_an_opendaylight_change_url(self) -> None:
        """OpenDaylight serves reviews under a different path prefix."""
        self.assertEqual(
            repository_url_from(
                "https://git.opendaylight.org/gerrit/c/docs/+/1", "docs"
            ),
            "https://git.opendaylight.org/gerrit/docs",
        )

    def test_keeps_a_nested_project_path(self) -> None:
        self.assertEqual(
            repository_url_from(ONAP_URL, "cps/ncmp-dmi-plugin"),
            "https://gerrit.onap.org/r/cps/ncmp-dmi-plugin",
        )

    def test_rejects_a_url_with_no_review_segment(self) -> None:
        with self.assertRaises(NamingError):
            _ = repository_url_from("https://gerrit.onap.org/", "cps")

    def test_creation_records_the_repository_not_the_change(self) -> None:
        client = FakeClient(projects={"onap-doc"})
        _ = run(fast(mode=MODE_MERGE), client)
        created = [c for c in client.calls if c.startswith("create:")]
        self.assertEqual(created, ["create:onap-cps"])
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


if __name__ == "__main__":
    _ = unittest.main(verbosity=2)
