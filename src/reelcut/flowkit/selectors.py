"""THE selector registry.

Every CSS string the driver uses lives here, as a named constant. Task scripts
reference these BY NAME (`locator_ref`) and never carry a literal selector — a
literal inside a script is two copies of the truth, and the lint rejects it.

All of these were confirmed against the live page. They anchor on accessible
names and Flow's own custom elements / semantic class names, never on the hashed
Angular attributes (`_ngcontent-ng-c1072636748`) which change every build, and
never on positional XPaths or `#cdk-overlay-N` ids (assigned per overlay and
incrementing). The policy is enforced in code by yta.trainer.lint.
"""

FLOW_HOME = 'https://flow.google.com/'

# --- the grid ---------------------------------------------------------------
TILE_SEL = 'flow-grid-tile-container'
SCROLL_VIEWPORT_SEL = 'cdk-virtual-scroll-viewport.tiles-container'
PROJECT_CARD_SEL = 'flow-project-card, div.project-card'

# --- prompt / generation ----------------------------------------------------
PROMPT_SEL = 'div.ProseMirror[contenteditable="true"]'
GEN_BTN_SEL = 'button[aria-label="Start generation"]'

# --- the project settings panel ---------------------------------------------
# The trigger sits in the composer footer (`tune`) and opens the generation
# panel; Back is the ONLY way out, because Escape is ignored by the panel.
#
# 2026-11-05: the page now renders TWO VISIBLE buttons whose accessible name
# is exactly "Settings" - the header's `settings_2` icon (which opens the TILE
# VIEW menu: View mode / grid size / hover toggles) and the composer footer's
# `tune` icon (the real panel, titled "Agent settings"). The hidden
# `aria-label="Settings trigger"` twin from the 2026-10-08 build is gone. A
# bare `button[aria-label="Settings"]` therefore resolves to 2 elements and
# `.first` picked the HEADER one: every run blocked in check_settings waiting
# for a model label that menu never shows. Scope to the composer panel, with
# the icon ligature as the fallback anchor (`:text-is` matches textContent,
# which for the button IS the ligature). Evidence: probe_settings_panels.py.
SETTINGS_BTN_SEL = ('.agent-panel-prompt-box button[aria-label="Settings"], '
                    'button[aria-label="Settings"]:text-is("tune")')
# The ladder `open_settings` walks, in order, when the primary selector does not
# resolve to a VISIBLE button. Both accounts' projects were live on 2026-11-05
# and their composers differ (b: docked panel, tune at x≈1439; a: tune at x≈983
# with a hidden duplicate matching the same aria-label earlier in the DOM, which
# is why `.first` timed out for 80s on account a). Each candidate is clicked and
# then VERIFIED by the panel opening, so a wrong pick costs a second, not a run.
SETTINGS_BTN_CANDIDATES = (
    '.agent-panel-prompt-box button[aria-label="Settings"]',
    'button[aria-label="Settings"]:text-is("tune")',
    # Agent OFF on this build swaps the composer to the direct IMAGE composer,
    # whose settings entry point is the config chip "Settings trigger"
    # ("🍌 Nano Banana 2.1 crop_9_16 x2") - the 2026-10-10 build shape that
    # SlideAgent already knew. Without these two, check_settings blocks the
    # moment Agent is OFF (measured 2026-11-06).
    'button[aria-label="Settings trigger"]',
    'button.settings-trigger-button',
    '.submit-controls button[aria-label="Settings"]',
    'button[aria-label="Settings"]',
)
SETTINGS_BACK_SEL = 'button[aria-label="Back"]'

# --- settings panel ---------------------------------------------------------
# LEGACY build only (pre-2026-11-05): a per-row model picker labelled
# "Omni 1.1 Flash". `settings.py` tries this shape FIRST so a build that
# regresses to it needs no code change; on the current build there is no
# video-model picker anywhere in the panel (only the image row has a model
# dropdown), so the model is recorded as unreadable instead of asserted.
VIDEO_MODEL_BTN = 'button.video-model-picker-button'
MODEL_LABEL_SEL = 'span.model-select-trigger-label'

# --- the "+" media menu (project-level uploads) ------------------------------
# The arc last-frame link uploads an extracted PNG here. This is the MAIN GRID
# page's "+" - NOT the composer's "Add ingredients to the prompt box", which is
# a different popover whose asset never becomes a tile. Menu: "upload Upload",
# "folder New collection", "account_circle Create character", "play_movies New
# scene". An upload lands as a normal grid tile, newest first, named after the
# FILE (extension included).
ADD_MEDIA_BTN = 'button[aria-label="Add media menu"]'

# --- ingredients attached to the prompt -------------------------------------
# Attaching is a right-click on the tile -> "Add to prompt". The chip lands in
# flow-ingredient-bar, NOT in the ProseMirror editor, so clear_prompt() does
# not remove it and a frame attached for part 2 would ride into part 3.
INGREDIENT_BAR = 'flow-ingredient-bar'
# With Video + Frames the same element becomes the start/end frame bar (class
# `prompt-ingredient-bar`): slots Start / Swap / End, each opening a
# "Select a frame image" picker over the project's own images (2026-11-06).
FRAME_BAR = 'flow-ingredient-bar'
FRAME_PICKER_TITLE = 'Select a frame image'
INGREDIENT_CHIP = 'flow-ingredient-bar button.chip-container'
INGREDIENT_CHIP_IMG = 'flow-ingredient-bar img.chip-image'

# --- settings panel ---------------------------------------------------------
VIDEO_MODEL_BTN = 'button.video-model-picker-button'
IMAGE_MODEL_BTN = 'button.image-model-picker-button'
MODEL_LABEL_SEL = 'span.model-select-trigger-label'
RADIO_GROUP_SEL = 'mat-radio-group'
MODEL_MENU_SEL = '[role="menu"] [role="menuitem"], mat-option'

# --- image mode (the slides/image-configured composer) -----------------------
# Ported from SlideAgent's sla/driver/selectors.py, where scripts/
# probe_image_mode.py confirmed them live on 2026-10-10 against THIS same
# slides project (b37ae557...). TWO build shapes are supported, because the
# build drifted between 2026-10-10 and 2026-11-05 (both observed live):
#
# OLD (2026-10-10): Agent is a CHIP. aria-pressed="false" + plain
#   `agent-mode-chip` = OFF -> a settings-trigger chip is visible and PRINTS
#   the image config, e.g. "🍌 Nano Banana 2.1 crop_9_16 x1".
#   aria-pressed="true" (+ `agent-mode-chip-checked`) = ON.
# NEW (2026-11-05, scripts/probe_agent_toggle.py): the chip classes are gone.
#   Agent is `button[aria-label="Agent instructions"]` carrying aria-pressed
#   (false = OFF - the image-capable state), beside a Settings (tune) button;
#   the image config lives inside the settings dialog, read via
#   IMAGE_MODEL_BTN_ARIA (never written - settings are check-only).
#
# `yta/driver/images.py` settles Agent OFF (idempotent read->click->verify)
# in either shape and asserts the image config before any shot.
AGENT_CHIP_SEL = 'button.agent-mode-chip'
AGENT_CHIP_CHECKED_SEL = 'button.agent-mode-chip.agent-mode-chip-checked'
SETTINGS_TRIGGER_SLIDES_SEL = ('button[aria-label="Settings trigger"], '
                               'button.settings-trigger-button')
VIDEO_AGENT_BTN = 'button[aria-label="Agent instructions"]'
# NOTE: the old settings dialog's IMAGE row picker
# ('button[aria-label="Image generation default model"]', sla P4a) is GONE on
# the 2026-11-05 build - the dialog became a preferences panel and the
# generation config is not exposed anywhere readable. The image pipeline
# verifies the config by its OUTPUT instead (aspect_problems on the rendered
# pixels); do not re-add a dialog-row selector until Flow exposes one again.

# --- collections (mapped live 2026-11-04, scripts/probe_collections*.py) -----
# There is NO context-menu path into a collection: an asset tile's menu has
# no collection item, and the collection view has no "add existing". The move
# is a DRAG of the asset tile ONTO the collection tile in the root grid - and
# it is a MOVE, not a reference copy (the dragged tile leaves All media, so
# download BEFORE moving). A collection is itself a grid tile whose menu is
# ['Favourite', 'Download collection', 'Rename', 'Move all contents to bin'];
# its view lives at /project/<uuid>/collection/<coll-uuid>. Deleting a
# collection BINS its contents ("Move all contents to bin", confirmed by a
# dialog naming exactly that). All of these are driven by LABEL through
# click_menu_item / rename_tile - no CSS anchor of their own, by design.

# --- overlays / dialogs -----------------------------------------------------
OVERLAY_SEL = '.cdk-overlay-backdrop, .cdk-overlay-popover'
BACKDROP_SEL = '.cdk-overlay-backdrop'
OVERLAY_CONTAINER_SEL = '.cdk-overlay-container'
DIALOG_SEL = '[role="dialog"], mat-dialog-container'
# Must be wrapped in :is() before being used as a descendant prefix: a bare
# comma list would bind only to the last branch, so f'{DIALOG_SEL} input' would
# degrade to "[role=dialog], mat-dialog-container input" and match the dialog
# container itself as if it were a field.
DIALOG_ANY = ':is([role="dialog"], mat-dialog-container)'

# --- rename editor ----------------------------------------------------------
# `.editing` is load-bearing: a bare `input.editable-text-input` ALSO matches the
# project-title field in the nav header, and Playwright's `.first` resolves to
# that one — the fill then types into the header while every read-back still
# looks right, because it reads back the field that was written.
RENAME_INPUT_SEL = 'input.editable-text-input.editing'
RENAME_DONE_SEL = 'button[aria-label="Done"]'      # the check next to the Cancel

# --- context menu -----------------------------------------------------------
MENU_SEL = '[role="menu"]'
MENU_ITEM_SEL = '[role="menuitem"]'

# --- account identity / credits ---------------------------------------------
# The identity label is an <a> inside Google's OneGoogle widget:
#   Google Account: <Name>  \n(<email>), Google membership
# READ ONLY — that element links to Google's sign-out page and must never be
# clicked. The panel button is a DIFFERENT element (see ACCOUNT_PANEL_BTN_SEL).
GOOGLE_ACCT_SEL = '[aria-label*="Google Account"]'
ACCOUNT_PANEL_SEL = 'flow-account-panel'
ACCOUNT_PANEL_BTN_SEL = '[aria-label="Account details"], .header-user-button'
CREDITS_SEL = 'flow-account-panel .credits-info-wrapper'

# --- values the pipeline requires -------------------------------------------
GEN_MODEL = 'Omni 1.1 Flash'  # the model the pipeline requires for video
ASPECT = '9:16'               # Shorts
CONFIRM_NEVER = 'Never'       # "generate media and spend credits automatically"

# What Flow shows once it has accepted the upload. Verified on a real publish:
# the dialog reads "Preparing video..." and no URL is shown in the dialog.
PUBLISH_ACCEPTED = 'preparing video'
