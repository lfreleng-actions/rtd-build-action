<!--
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
-->

# 📚 ReadTheDocs Build

<!-- prettier-ignore-start -->
<!-- markdownlint-disable-next-line MD013 -->
[![Linux Foundation](https://img.shields.io/badge/Linux-Foundation-blue)](https://linuxfoundation.org/) [![Source Code](https://img.shields.io/badge/GitHub-100000?logo=github&logoColor=white&color=blue)](https://github.com/lfreleng-actions/rtd-build-action) [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0) [![pre-commit.ci status badge]][pre-commit.ci results page] [![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/lfreleng-actions/rtd-build-action/badge)](https://scorecard.dev/viewer/?uri=github.com/lfreleng-actions/rtd-build-action)
<!-- prettier-ignore-end -->

## rtd-build-action

Verifies or publishes a project's documentation on ReadTheDocs.

The action runs two lanes. **Verify** reports what a merge would do and
changes nothing, which suits a patchset check. **Merge** creates the
project when absent, attaches it to the umbrella project, sets the
landing version, triggers a build and waits for the result.

It drives the ReadTheDocs API through
[`lftools-uv`][lftools-uv], run via `uvx` so that release-engineering
tooling stays out of the interpreter a documentation build uses.

## Usage Example

<!-- markdownlint-disable MD046 -->

```yaml
steps:
  - uses: astral-sh/setup-uv@v6

  - name: "Publish documentation"
    uses: lfreleng-actions/rtd-build-action@main
    with:
      mode: "merge"
      rtd_token: ${{ secrets.RTD_TOKEN }}
      gerrit_project: ${{ inputs.GERRIT_PROJECT }}
      gerrit_change_url: ${{ inputs.GERRIT_CHANGE_URL }}
      branch: ${{ inputs.GERRIT_BRANCH }}
```

<!-- markdownlint-enable MD046 -->

A patchset check needs the mode change alone:

<!-- markdownlint-disable MD046 -->

```yaml
  - name: "Check documentation configuration"
    uses: lfreleng-actions/rtd-build-action@main
    with:
      mode: "verify"
      rtd_token: ${{ secrets.RTD_TOKEN }}
      gerrit_project: ${{ inputs.GERRIT_PROJECT }}
      gerrit_change_url: ${{ inputs.GERRIT_CHANGE_URL }}
```

<!-- markdownlint-enable MD046 -->

## Name resolution

The action derives the ReadTheDocs slugs so a caller supplies nothing
beyond the Gerrit values it already holds.

The umbrella comes from the change URL host. A URL on
`gerrit.onap.org` yields `onap`, one on `gerrit.o-ran-sc.org` yields
`o-ran-sc`, and one on `git.opendaylight.org` yields `opendaylight`.

| Value | Derivation |
| ----- | ---------- |
| Project | `<umbrella>-<gerrit project>`, joined with hyphens |
| Umbrella project | `<umbrella>-<parent_suffix>` |
| Version | The branch name, slugified |

Two automatic fallbacks handle an organisation that imported existing
documentation, where the top-level docs sit under the bare umbrella name
rather than a suffixed one:

- When `<umbrella>-doc` is absent but `<umbrella>` exists, the umbrella
  project resolves to `<umbrella>`.
- When the change targets the umbrella's own documentation repository and
  the derived project is absent, it resolves the same way.

Both probe the API rather than carrying a hardcoded list, so no project
needs configuration to benefit.

Set `project` or `parent_project` to bypass derivation, or supply
`project_overrides` as `from=to` pairs for a project following no
derivable rule.

## Branch names and version slugs

ReadTheDocs addresses a version by slug: it lowercases the branch name
and replaces every character outside `[a-z0-9._-]` with a hyphen. A
branch named `maintenance/3.7.10` lives at `maintenance-3.7.10`.

Passing a raw branch name produces a request path that does not resolve.
The action slugifies every branch before it reaches the API.

The repository's default branch is a separate case. ReadTheDocs tracks
that branch under its `latest` alias rather than giving it a version slug,
so a request for a version named `master` addresses something that does
not exist. The action maps `master` and `main` onto the default version
automatically; set `default_branch` when a repository uses another name.

## Inputs

<!-- markdownlint-disable MD013 -->

| Input               | Required | Default   | Description                                                  |
| ------------------- | -------- | --------- | ------------------------------------------------------------ |
| `mode`              | True     |           | Lane to run: `verify` or `merge`                             |
| `rtd_token`         | True     |           | ReadTheDocs API token                                        |
| `gerrit_project`    | False    |           | Gerrit project path, e.g. `cps` or `cps/ncmp-dmi-plugin`     |
| `gerrit_change_url` | False    |           | Gerrit change URL; supplies the umbrella name                |
| `branch`            | False    |           | Branch under change                                          |
| `default_branch`    | False    |           | Branch published as the default version; empty accepts both  |
| `project`           | False    |           | ReadTheDocs project slug; empty derives one                  |
| `parent_project`    | False    |           | Umbrella project slug; empty derives one                     |
| `parent_suffix`     | False    | `doc`     | Suffix appended to the umbrella name for its docs project    |
| `project_overrides` | False    |           | Newline or comma separated `from=to` slug rewrites           |
| `default_version`   | False    | `latest`  | Version ReadTheDocs serves as the landing page               |
| `repository_url`    | False    |           | Repository URL recorded when creating a project              |
| `homepage`          | False    |           | Homepage URL recorded when creating a project                |
| `build_timeout`     | False    | `1800`    | Seconds to wait for a documentation build                    |
| `create_timeout`    | False    | `600`     | Seconds to wait for a new project to appear                  |
| `poll_interval`     | False    | `10`      | Seconds between polls while waiting                          |
| `lftools_version`   | False    | `0.5.3`   | Version of `lftools-uv` to run                               |
| `summary`           | False    | `true`    | Write a report to the workflow step summary                  |

<!-- markdownlint-enable MD013 -->

## Outputs

<!-- markdownlint-disable MD013 -->

| Output               | Description                                            |
| -------------------- | ------------------------------------------------------ |
| `project`            | ReadTheDocs project slug the action used               |
| `parent_project`     | Umbrella project slug the action used                  |
| `version_slug`       | Version slug the action built                          |
| `project_exists`     | Whether the project exists on ReadTheDocs              |
| `project_created`    | Whether this run created the project                   |
| `subproject_created` | Whether this run attached the project to its umbrella  |
| `version_activated`  | Whether this run made a discovered version visible     |
| `build_id`           | Identifier of the documentation build, when one ran    |
| `build_success`      | Whether the documentation build succeeded              |
| `documentation_url`  | URL of the published documentation                     |
| `outcome_json`       | Complete outcome as a JSON string                      |

<!-- markdownlint-enable MD013 -->

## Requirements

The action needs `uvx` on `PATH`. Add
[`astral-sh/setup-uv`](https://github.com/astral-sh/setup-uv) before it.

Pin `lftools_version` to keep runs reproducible. Version 0.5.3 is the
first release carrying the `--json` output the action reads.

## Merge lane sequence

```text
resolve names
  -> create project when absent, wait until visible
  -> attach to umbrella when the two differ
  -> set the landing version when it differs
  -> build the branch
       (build the default version first when the branch is unknown)
  -> activate a newly discovered version
```

A failed build fails the action. Every step reports through the step
summary and the `outcome_json` output.

## Testing

The suite runs against a fake client, so it needs no credentials and no
network access:

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

Coverage spans slug conversion, umbrella derivation for three
organisations, both fallbacks, the verify lane leaving the API untouched,
project and subproject creation, landing-version handling, branch
discovery, version activation and a failing build.

[lftools-uv]: https://github.com/lfreleng-actions/lftools-uv

[pre-commit.ci results page]: https://results.pre-commit.ci/latest/github/lfreleng-actions/rtd-build-action/main
<!-- markdownlint-disable-next-line MD013 -->
[pre-commit.ci status badge]: https://results.pre-commit.ci/badge/github/lfreleng-actions/rtd-build-action/main.svg
