import pytest

from app import FileListerApp


class FakeVar:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeTree:
    def __init__(self):
        self.rows = []
        self.selection_items = []

    def delete(self, *items):
        self.rows = []
        self.selection_items = []

    def get_children(self):
        return list(self.rows)

    def selection(self):
        return list(self.selection_items)

    def selection_remove(self, items):
        self.selection_items = []


class FakeWidget:
    def __init__(self):
        self.state = None

    def configure(self, state=None):
        self.state = state


def test_reset_scan_folder_tab_restores_defaults():
    app = FileListerApp.__new__(FileListerApp)
    app.scan_tree = FakeTree()
    app.scan_item_map = {"row": {"name": "demo"}}
    app.scan_results = [{"name": "demo"}]
    app.scan_folder_path = FakeVar("/tmp/source")
    app.scan_dest_path = FakeVar("/tmp/dest")
    app.scan_include_subdirs = FakeVar(False)
    app.scan_match_ext = FakeVar(False)
    app.scan_match_size = FakeVar(True)
    app.scan_operation = FakeVar("move")
    app.scan_add_to_db = FakeVar(True)
    app.scan_files_count_var = FakeVar("Files: 3")
    app.scan_matches_count_var = FakeVar("Matched Files: 2")
    app.scan_progress_var = FakeVar("Working")
    app.scan_progress_bar = FakeWidget()
    app.status_var = FakeVar("Busy")
    app.scan_add_to_db_checkbox = FakeWidget()
    app.scan_edit_dest_button = FakeWidget()
    app.scan_copy_move_button = FakeWidget()
    app.scan_inline_entry = (object(), "row")
    app.scan_operation_in_progress = True

    app._set_folder_scan_controls = lambda enabled: None

    FileListerApp.reset_scan_folder_tab(app)

    assert app.scan_folder_path.get() == ""
    assert app.scan_dest_path.get() == ""
    assert app.scan_include_subdirs.get() is True
    assert app.scan_match_ext.get() is True
    assert app.scan_match_size.get() is False
    assert app.scan_operation.get() == "copy"
    assert app.scan_add_to_db.get() is False
    assert app.scan_files_count_var.get() == "Files: 0"
    assert app.scan_matches_count_var.get() == "Matched Files: 0"
    assert app.scan_progress_var.get() == ""
    assert app.scan_tree.get_children() == []
    assert app.scan_item_map == {}
    assert app.scan_results == []
    assert app.scan_operation_in_progress is False
    assert app.scan_inline_entry is None
