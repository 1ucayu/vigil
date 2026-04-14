"""Widget-type guard template table for DSL generation.

Loaded from configs/android_platform.yaml. See that file for the complete
Android SDK widget reference organized by guard behavior pattern.
"""

from vigil.core.platform_priors import get_guard_template, get_widget_templates

__all__ = ["get_guard_template", "get_widget_templates"]
