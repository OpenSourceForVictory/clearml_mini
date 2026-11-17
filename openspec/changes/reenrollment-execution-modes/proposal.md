# Re-enrollment execution modes

SSH re-enrollment displayed an execution-mode choice, but the installer always retained each Worker's prior `execution_modes`. A selected host or container mode could therefore fail scheduler preflight after successful enrollment.

Make retention explicit and allow an administrator to change execution modes during re-enrollment. Keep per-device sharing approval independent and preserve the original configuration on installation failure.
