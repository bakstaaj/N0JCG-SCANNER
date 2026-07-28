#!/usr/bin/env python3
"""Stable service entry point for the dedicated UHF FFT scanner."""

from .uhf_fft_scanner import main

if __name__ == "__main__":
    raise SystemExit(main())
