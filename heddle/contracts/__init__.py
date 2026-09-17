"""heddle.contracts — stdlib-only contract leaves shared by every layer.

``result`` (the envelope: HeddleResult/ExitCode/ERROR_CODES) and
``schemas`` (vocabulary constants + the state-schema registry) originally lived
under ``heddle/runtime/`` before the kernel existed; the kernel's
import discipline then had to carve them out as named exemptions.
Relocated here so the layering is honest: kernel,
runtime, gate, and driver all import contracts; nothing imports upward.
Both modules stay stdlib-only leaves — this package must never grow a
dependency on any other ``heddle`` package.
"""
