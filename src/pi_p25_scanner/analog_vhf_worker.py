#!/usr/bin/env python3
"""Stable module entry point for the FFT-directed VHF NFM worker."""

from .vhf_fft_scanner import main


if __name__ == "__main__":
    raise SystemExit(main())
