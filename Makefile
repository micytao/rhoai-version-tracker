# RHOAI Version Tracker — local pipeline targets, mirroring the "Local setup"
# section in README.md. Targets run through the .venv interpreter so nothing
# needs a manual `source .venv/bin/activate`; the venv is created on first
# use (make setup, or implicitly by any target that needs it).
#
# Release-notes PDFs are git-ignored (see .gitignore) — drop your own copy at
# data/raw/<version>.pdf before running `extract`, e.g.:
#     make extract VERSION=3.6
# Preview the built site after `build` with `make serve` (default port 8765).

PY       ?= .venv/bin/python
VERSION  ?= 3.5
PDF      ?= data/raw/$(VERSION).pdf
PORT     ?= 8765

.PHONY: help setup extract fetch-matrix validate build serve all

help:
	@echo "RHOAI Version Tracker — local pipeline targets:"
	@echo "  make setup         create .venv and install requirements.txt"
	@echo "  make extract       release-notes PDF -> data/extracted/<version>.json (VERSION=<v>, default $(VERSION))"
	@echo "  make fetch-matrix  live Supported Configurations matrix -> data/matrix_snapshots/"
	@echo "  make validate      schema + sanity gate on the registry"
	@echo "  make build         render the static site into docs/"
	@echo "  make serve         preview docs/ at http://localhost:$(PORT)"
	@echo "  make all           extract -> fetch-matrix -> validate -> build"

setup: $(PY)

$(PY):
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

extract: $(PY)
	$(PY) scripts/extract_release_notes.py $(PDF)

fetch-matrix: $(PY)
	$(PY) scripts/fetch_support_matrix.py

validate: $(PY)
	$(PY) scripts/validate_registry.py

build: $(PY)
	$(PY) scripts/build_site.py

serve: $(PY)
	$(PY) -m http.server $(PORT) --directory docs

# The full Local setup sequence. `extract` needs its PDF present:
# data/raw/$(VERSION).pdf is not committed — download it from
# docs.redhat.com first, or skip straight to validate/build.
all: extract fetch-matrix validate build
