"""Adds an assembly panel to the Build Order page.

The panel embeds the InteractiveHtmlBom attached to that build order and
turns placements into real stock consumption. All of the interesting logic
lives in the frontend (see ../frontend/), because that is where the
authenticated, same-origin `api` client already is -- this class only
declares the panel and hands over the build's id.

Panel state (which designators are placed, sourced, DNP, lost, and which have
actually been consumed) is stored in this build's plugin metadata rather than
in the browser, so a build can be picked up on a different machine.

Requires InvenTree >= 1.3 and the ENABLE_PLUGINS_INTERFACE global setting.
"""

from django.utils.translation import gettext_lazy as _

from plugin import InvenTreePlugin
from plugin.mixins import SettingsMixin, UserInterfaceMixin

from . import __version__ as PLUGIN_VERSION

PLUGIN_SLUG = "build-ibom"


class BuildIbomPlugin(SettingsMixin, UserInterfaceMixin, InvenTreePlugin):
    """Interactive PCB assembly against a Build Order."""

    NAME = "BuildIbom"
    SLUG = PLUGIN_SLUG
    TITLE = _("Build iBOM")
    DESCRIPTION = _(
        "Assemble a PCB against a Build Order using InteractiveHtmlBom, "
        "consuming allocated stock as parts are placed"
    )
    VERSION = PLUGIN_VERSION
    AUTHOR = "Sam Turner"
    MIN_VERSION = "1.3.0"

    SETTINGS = {}

    def get_ui_panels(self, request, context, **kwargs):
        """Show the assembly panel on Build Order detail pages only.

        `context` carries the page's model and id; the panel is irrelevant
        anywhere else, and returning nothing keeps it off those pages.
        """
        if context.get("target_model") != "build":
            return []

        return [
            {
                "key": "build-ibom-assembly",
                "title": _("Assembly"),
                "description": _("Place parts and consume stock"),
                "icon": "ti:circuit-board:outline",
                "feature_type": "panel",
                "source": self.plugin_static_file("panel.js:renderPanel"),
                "context": {
                    "build_id": context.get("target_id"),
                },
            }
        ]
