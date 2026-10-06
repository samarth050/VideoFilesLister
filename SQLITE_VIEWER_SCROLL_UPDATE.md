# SQLite Viewer UI Update

## Changes

Implemented on the latest FileListerModularPortable source:

1. Added a vertical outer scrollbar to the complete SQLite Viewer tab.
   - All existing controls remain unchanged.
   - Mouse-wheel scrolling works over the viewer.
   - The SQLite record Treeview keeps its own native row scrolling.
   - Other tabs are not affected.

2. Increased SQLite Viewer record-list height:
   - Previous: 220 px
   - New: 340 px
   - This gives substantially more records visible at once.
   - The outer scrollbar provides access to the Movie Metadata section below.

3. Increased Cover preview display size proportionately:
   - Previous: 150 x 190
   - New: 210 x 266
   - Existing image-loading and metadata behavior is unchanged.

## Validation

- `app.py` compiles successfully.
- Existing test suite: 25 passed.
- No changes were made to `videosnapper/` or `statistics_dashboard.py`.
