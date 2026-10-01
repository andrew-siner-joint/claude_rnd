# Builds the CPU harness for BlinkFlare.blink.
# The kernel TU is compiled with strict float warnings so double literals or
# implicit narrowing (which Blink may reject or silently slow down) fail here.
KERNEL ?= ../../blinkflare/kernel/BlinkFlare.blink
BUILD  ?= build
CXX    ?= g++
CXXFLAGS ?= -O2 -std=c++17 -pthread
STRICT = -Wall -Wextra -Wshadow -Wconversion -Wdouble-promotion -Wfloat-conversion -Werror

$(BUILD)/blinkflare_render: $(BUILD)/kernel_tu.o $(BUILD)/shim_impl.o $(BUILD)/main.o
	$(CXX) $(CXXFLAGS) -o $@ $^

$(BUILD)/kernel_tu.o: kernel_tu.cpp blink_shim.h kernel_api.h $(KERNEL) | $(BUILD)
	$(CXX) $(CXXFLAGS) $(STRICT) -DKERNEL_SOURCE='"$(abspath $(KERNEL))"' -DKERNEL_CLASS=BlinkFlareKernel -c $< -o $@

$(BUILD)/%.o: %.cpp blink_shim.h kernel_api.h | $(BUILD)
	$(CXX) $(CXXFLAGS) -Wall -Wextra -c $< -o $@

$(BUILD):
	mkdir -p $(BUILD)

clean:
	rm -rf $(BUILD)

.PHONY: clean
