# Development and release tasks. `make check` runs the CI checks locally;
# `make release VERSION=X.Y.Z` sets the version everywhere it lives, runs the
# checks, commits, tags vX.Y.Z and pushes (the tag triggers the PyPI publish
# workflow). Set UV=/path/to/uv when the `uv` on PATH is not the one to use.

UV ?= uv
REMOTE ?= origin
BRANCH ?= main
TAG = v$(VERSION)
TODAY := $(shell date +%Y-%m-%d)
# X.Y.Z with an optional PEP 440 pre-release (a1, b1, rc1), .postN or .devN suffix.
VERSION_PATTERN = ^[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?(\.post[0-9]+)?(\.dev[0-9]+)?$$

.PHONY: check release

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
	@test -z "$$(git status --porcelain)" || { echo "the working tree is not clean; commit or stash first:" >&2; git status --short >&2; exit 2; }
	@! git rev-parse -q --verify "refs/tags/$(TAG)" >/dev/null || { echo "tag $(TAG) already exists locally" >&2; exit 2; }
	@test -z "$$(git ls-remote --tags $(REMOTE) "refs/tags/$(TAG)")" || { echo "tag $(TAG) already exists on $(REMOTE)" >&2; exit 2; }
	@grep -Eq '^## .* \(unreleased\)$$' CHANGELOG.md || { echo "CHANGELOG.md needs a '## $(VERSION) (unreleased)' section holding this release's notes" >&2; exit 2; }
	sed -i.bak -E 's/^version = "[^"]*"$$/version = "$(VERSION)"/' pyproject.toml && rm pyproject.toml.bak
	@grep -Fxq 'version = "$(VERSION)"' pyproject.toml || { echo "pyproject.toml: the version line was not updated" >&2; exit 1; }
	sed -i.bak -E '1,/^## .* \(unreleased\)$$/ s/^## .* \(unreleased\)$$/## $(VERSION) ($(TODAY))/' CHANGELOG.md && rm CHANGELOG.md.bak
	@grep -Fxq '## $(VERSION) ($(TODAY))' CHANGELOG.md || { echo "CHANGELOG.md: the unreleased heading was not updated" >&2; exit 1; }
	$(UV) lock
	$(MAKE) check
	git add pyproject.toml uv.lock CHANGELOG.md
	git commit -m "Release $(VERSION)"
	git tag -a "$(TAG)" -m "tcnc $(VERSION)"
	git push $(REMOTE) $(BRANCH)
	git push $(REMOTE) "$(TAG)"
	@echo "Released $(VERSION): $(TAG) is on $(REMOTE); the publish workflow checks, builds and uploads it to PyPI."
