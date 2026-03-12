"""Shared command processor for output history. Used by text_handler."""


class CommandProcessor:
    """Holds history for delete_last. Used by text_handler."""
    def __init__(self):
        self.history = []

    def record_output(self, out: str):
        if out and out != "\n":
            self.history.append(out)


processor = CommandProcessor()
