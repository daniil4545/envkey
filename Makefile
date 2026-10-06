PREFIX ?= $(HOME)/bin
COMMANDS := withkey keyls keyset keydel keymv

.PHONY: test install

test:
	python3 -m unittest discover -s . -v 2>&1 | tail -40

install:
	mkdir -p $(PREFIX)
	for c in $(COMMANDS); do ln -sfn $(CURDIR)/$$c $(PREFIX)/$$c; done
	@echo "installed to $(PREFIX)"
