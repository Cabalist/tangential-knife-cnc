# Development and release tasks. `make check` runs the CI checks locally;
# `make release VERSION=X.Y.Z` sets the version everywhere it lives, runs the
# checks, commits, tags vX.Y.Z and pushes (the tag triggers the PyPI publish
# workflow). A rerun finds the bump already in place and goes on from there;
# with the bump still uncommitted, `make release-tag VERSION=X.Y.Z` does the
# commit, tag and push. Set UV=/path/to/uv when the `uv` on PATH is not the
# one to use.

UV ?= uv
REMOTE ?= origin
BRANCH ?= main
TAG = v$(VERSION)
TODAY := $(shell date +%Y-%m-%d)
# X.Y.Z with an optional PEP 440 pre-release (a1, b1, rc1), .postN or .devN suffix.
VERSION_PATTERN = ^[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?(\.post[0-9]+)?(\.dev[0-9]+)?$$

.PHONY: check release release-tag

check:
	$(UV) run --locked ruff format --check .
	$(UV) run --locked ruff check src tests stubs
	$(UV) run --locked ty check
	$(UV) run --locked pyrefly check
	$(UV) run --locked pytest

release:
	@test -n "$(VERSION)" || { echo "usage: make release VERSION=X.Y.Z" >&2; exit 2; }
	@echo "$(VERSION)" | grep -Eq '$(VERSION_PATTERN)' || { echo "VERSION must look like 1.2.3 (a1, b1, rc1, .post1 or .dev1 may follow), got '$(VERSION)'" >&2; exit 2; }
	@test "$$(git rev-parse --abbrev-ref HEAD)" = "$(BRANCH)" || { echo "release from $(BRANCH); the current branch is $$(git rev-parse --abbrev-ref HEAD)" >&2; exit 2; }
	@test -z "$$(git status --porcelain)" || { echo "the working tree is not clean; commit or stash first (a bump left by an earlier run is finished with: make release-tag VERSION=$(VERSION)):" >&2; git status --short >&2; exit 2; }
	@! git rev-parse -q --verify "refs/tags/$(TAG)" >/dev/null || { echo "tag $(TAG) already exists locally" >&2; exit 2; }
	@test -z "$$(git ls-remote --tags $(REMOTE) "refs/tags/$(TAG)")" || { echo "tag $(TAG) already exists on $(REMOTE)" >&2; exit 2; }
	@if grep -Fxq 'version = "$(VERSION)"' pyproject.toml && grep -Eq '^## $(VERSION) \([0-9]{4}-[0-9]{2}-[0-9]{2}\)$$' CHANGELOG.md; then \
	  echo "$(VERSION) is already set in pyproject.toml and CHANGELOG.md; going on to lock, check, commit, tag and push"; \
	else \
	  grep -Eq '^## .* \(unreleased\)$$' CHANGELOG.md || { echo "CHANGELOG.md needs a '## $(VERSION) (unreleased)' section holding this release's notes" >&2; exit 2; }; \
	  sed -i.bak -E 's/^version = "[^"]*"$$/version = "$(VERSION)"/' pyproject.toml && rm pyproject.toml.bak; \
	  grep -Fxq 'version = "$(VERSION)"' pyproject.toml || { echo "pyproject.toml: the version line was not updated" >&2; exit 1; }; \
	  sed -i.bak -E '1,/^## .* \(unreleased\)$$/ s/^## .* \(unreleased\)$$/## $(VERSION) ($(TODAY))/' CHANGELOG.md && rm CHANGELOG.md.bak; \
	  grep -Fxq '## $(VERSION) ($(TODAY))' CHANGELOG.md || { echo "CHANGELOG.md: the unreleased heading was not updated" >&2; exit 1; }; \
	fi
	$(UV) lock
	$(MAKE) check
	$(MAKE) release-tag VERSION=$(VERSION)

release-tag:
	@test -n "$(VERSION)" || { echo "usage: make release-tag VERSION=X.Y.Z" >&2; exit 2; }
	@grep -Fxq 'version = "$(VERSION)"' pyproject.toml || { echo "pyproject.toml does not say $(VERSION); run make release" >&2; exit 2; }
	@grep -Eq '^## $(VERSION) \([0-9]{4}-[0-9]{2}-[0-9]{2}\)$$' CHANGELOG.md || { echo "CHANGELOG.md has no dated '## $(VERSION) (YYYY-MM-DD)' heading; run make release" >&2; exit 2; }
	@! git rev-parse -q --verify "refs/tags/$(TAG)" >/dev/null || { echo "tag $(TAG) already exists locally" >&2; exit 2; }
	@test -z "$$(git ls-remote --tags $(REMOTE) "refs/tags/$(TAG)")" || { echo "tag $(TAG) already exists on $(REMOTE)" >&2; exit 2; }
	git add pyproject.toml uv.lock CHANGELOG.md
	@git diff --cached --quiet && echo "the release files are already committed" || git commit -m "Release $(VERSION)"
	git tag -a "$(TAG)" -m "tcnc $(VERSION)"
	git push $(REMOTE) $(BRANCH)
	git push $(REMOTE) "$(TAG)"
	@echo "Released $(VERSION): $(TAG) is on $(REMOTE); the publish workflow checks, builds and uploads it to PyPI."
