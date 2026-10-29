# SPDX-License-Identifier: GPL-3.0-only
.PHONY: test test-slot build public-check source-release
test:
	python3 -m unittest discover -s tests -v
test-slot:
	python3 advanced/slot-switch/test_slot_helper.py
build:
	python3 tools/build_uboot.py
public-check:
	python3 tools/check_public.py
source-release:
	python3 tools/make_source_release.py
