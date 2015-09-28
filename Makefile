PYTHON=venv/bin/python3.5
PIP=venv/bin/pip
EI=venv/bin/easy_install
NOSE=venv/bin/nosetests
FLAKE=venv/bin/flake8

build:
	$(PIP) install -e hg+https://fafhrd91@code.google.com/p/tulip/#egg=tulip

env:
	python3.5 -m venv venv
	$(PYTHON) ./setup.py develop

dev:
	$(PIP) install flake8 nose coverage
	$(PYTHON) ./setup.py develop

flake:
	$(FLAKE) --exclude=./venv ./

test:
	$(NOSE) -s $(FLAGS) ./tests/

vtest:
	$(NOSE) -s -v $(FLAGS) ./tests/

clean:
	rm -rf `find . -name __pycache__`
	rm -f `find . -type f -name '*.py[co]' `
	rm -f `find . -type f -name '*~' `
	rm -f `find . -type f -name '.*~' `
	rm -f `find . -type f -name '@*' `
	rm -f `find . -type f -name '#*#' `
	rm -f `find . -type f -name '*.orig' `
	rm -f `find . -type f -name '*.rej' `
	rm -f .coverage
	rm -rf coverage
	rm -rf build


.PHONY: all build venv flake test vtest testloop cov clean
