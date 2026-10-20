.PHONY: deps build build-docker debs artwork run run-uefi run-secure-boot run-install run-installed reset-vm \
	test test-docker test-e2e move cloud-deps cloud-dev cloud-check cloud-deploy try-center try-store try-move clean

HOST_PACKAGES = debootstrap debian-archive-keyring squashfs-tools xorriso apt-utils dpkg-dev \
	grub-pc-bin grub-efi-amd64-bin grub-common mtools dosfstools qemu-system-x86 qemu-utils ovmf python3-pil librsvg2-bin \
	python3-gi gir1.2-adw-1 python3-cryptography python3-qrcode python3-zstandard python3-pytest rclone

# Run the Cherry apps from the source tree with a throwaway home, so trying them on this PC
# never touches your real settings.
TRY_ENV = HOME=$(CURDIR)/build/try-home PYTHONPATH=$(CURDIR)/desktop/lib CHERRY_ROOT=$(CURDIR)/build/try-root

deps:
	sudo apt-get install -y $(HOST_PACKAGES)

build:
	sudo ./build.sh

# Same build inside a Debian container: no host build tools or sudo needed, just Docker.
build-docker:
	./scripts/build-in-docker.sh

# Cherry's own .debs, into build/debs.
debs:
	./scripts/build-debs.sh build/debs

# Publishes Cherry Cloud (the store).
cloud-deploy:
	cd cloud && npx wrangler d1 migrations apply DB --remote && npx wrangler deploy

artwork:
	python3 scripts/make-artwork.py build/art

# Try the live system.
run:
	./scripts/run-qemu.sh

run-uefi:
	./scripts/run-qemu.sh --uefi

run-secure-boot:
	./scripts/run-qemu.sh --secure-boot

# Rehearse a real install onto a blank 64 GB virtual SSD.
run-install:
	./scripts/run-qemu.sh --uefi --disk

# Boot what was installed by run-install, without the ISO.
run-installed:
	./scripts/run-qemu.sh --uefi --installed

# Wipe the virtual SSD and UEFI settings to start the install rehearsal over.
reset-vm:
	rm -rf build/vm

UNIT_TESTS = tests/test_units.py tests/test_store.py tests/test_transfer.py tests/test_remote.py tests/test_move_app.py \
	tests/test_move_everything.py tests/test_system_extras.py

test:
	PYTHONPATH=desktop/lib python3 -m pytest -q $(UNIT_TESTS)

# The same tests inside Debian trixie (Cherry OS's own Python packages and rclone), needing only Docker.
# The Windows/macOS app's cross-test uses build/move (make move) since the container has no Go.
test-docker: move
	docker run --rm --network host -u 0 -v "$(CURDIR):$(CURDIR)" -w "$(CURDIR)" \
	  -e CHERRY_MOVE_BIN=build/move/cherry-move-linux-x86_64 debian:trixie sh tests/in-trixie.sh -q $(UNIT_TESTS)

# The Cherry Move app and its installers for the old computer (Windows, macOS, Linux) into build/move;
# needs Go 1.22+ and Docker.
# build.sh puts it in a CherryMove folder on the ISO.
move:
	./scripts/build-move.sh

# Store round trips against a local Cherry Cloud (run `make cloud-dev` first).
test-e2e:
	CHERRY_CLOUD_URL=http://localhost:8790 PYTHONPATH=desktop/lib python3 -m pytest -q tests/test_e2e.py

cloud-deps:
	cd cloud && npm install

cloud-dev:
	cd cloud && npx wrangler d1 migrations apply DB --local && npx wrangler dev --port 8790

cloud-check:
	cd cloud && npx tsc --noEmit

try-center:
	$(TRY_ENV) python3 desktop/bin/cherry-center

try-store:
	$(TRY_ENV) CHERRY_CLOUD_URL=http://localhost:8790 python3 desktop/bin/cherry-store

try-move:
	$(TRY_ENV) python3 desktop/bin/cherry-move

clean:
	sudo ./build.sh --clean
