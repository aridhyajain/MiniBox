# minibox Makefile
CC      := gcc
CFLAGS  := -std=c11 -Wall -Wextra -Werror -g -Iinclude -MMD -MP
BUILD   := build
TARGET  := minibox
SRCS    := src/main.c src/commands.c src/options.c src/monitor.c src/state.c \
           src/container.c src/rootfs.c src/net.c src/cgroup.c src/exec.c src/minit.c
OBJS    := $(SRCS:src/%.c=$(BUILD)/%.o)

.PHONY: all clean check-env rootfs test test-all test-phase3 test-phase4 test-phase4b test-phase5 test-phase6 test-init test-bench bench bench-quick charts ui-setup ui-run demo

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS)

$(BUILD)/%.o: src/%.c | $(BUILD)
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILD):
	mkdir -p $(BUILD)

check-env:
	@./scripts/check_env.sh

rootfs:
	@./scripts/get_rootfs.sh

# Tests need root (namespaces, mounts); make never runs sudo itself.
test: $(TARGET)
	@echo "Tests need root. Run:"
	@echo "  sudo tests/phase1_test.sh"
	@echo "  sudo tests/phase2_test.sh"
	@echo "  sudo tests/phase3_test.sh"
	@echo "  sudo tests/phase4_test.sh"
	@echo "  sudo tests/phase4b_test.sh"
	@echo "  sudo tests/phase5_test.sh     (needs 'make ui-setup' first)"
	@echo "  sudo bash tests/init_test.sh"
	@echo "or all of them in one go:  make test-all"

# One command for every test script (runs them all, prints a summary).
test-all: $(TARGET)
	@echo "All tests need root. Run:"
	@echo "  sudo bash tests/run_all.sh"

test-phase3: $(TARGET)
	@echo "Phase 3 tests need root. Run:"
	@echo "  sudo tests/phase3_test.sh"

test-phase4: $(TARGET)
	@echo "Phase 4 tests need root. Run:"
	@echo "  sudo tests/phase4_test.sh"

test-init: $(TARGET)
	@echo "The --init tests need root. Run:"
	@echo "  sudo bash tests/init_test.sh"

test-phase4b: $(TARGET)
	@echo "Phase 4b tests need root. Run:"
	@echo "  sudo tests/phase4b_test.sh"

test-phase5: $(TARGET)
	@echo "Phase 5 tests need root and the UI environment. Run:"
	@echo "  scripts/setup_ui.sh          # once, as your normal user (no sudo)"
	@echo "  sudo tests/phase5_test.sh"
	@echo "Unit tests (no root):  python3 -m unittest tests/test_ui_validation.py -v"

# The web UI: make only prints the commands; setup needs no root, running the UI does.
ui-setup:
	@echo "Create the UI's Python environment (no sudo; needs internet for pip):"
	@echo "  scripts/setup_ui.sh"

ui-run: $(TARGET)
	@echo "The UI must run as root (minibox needs it) and listens on 127.0.0.1 only. Run:"
	@echo "  sudo scripts/run_ui.sh 8080"
	@echo "Then open the URL it prints (it contains the access token)."

demo: $(TARGET)
	@echo "Review 1 demo needs root. Run:"
	@echo "  sudo scripts/demo_review1.sh          # press Enter between steps"
	@echo "  sudo scripts/demo_review1.sh --auto   # 2 s pause per step (recording)"

# Phase 6: benchmarks. They need root; make prints the command. Charts and unit tests need no root.
test-phase6: $(TARGET)
	@echo "Benchmark tooling check needs root. Run:"
	@echo "  sudo bash tests/phase6_test.sh"

test-bench:
	python3 -m unittest tests/test_bench_stats.py -v

bench: $(TARGET)
	@echo "Full benchmark (N=30, a few minutes) needs root. Run:"
	@echo "  sudo python3 bench/bench.py all"

bench-quick: $(TARGET)
	@echo "Quick benchmark (N=5) needs root. Run:"
	@echo "  sudo python3 bench/bench.py all --quick"

charts:
	python3 bench/make_charts.py bench/results/latest

clean:
	rm -rf $(BUILD) $(TARGET)

-include $(OBJS:.o=.d)
