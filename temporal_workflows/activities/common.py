class FileActivityError(Exception):
    pass


class DownloadError(FileActivityError):
    pass


class ParseError(FileActivityError):
    pass


class IngestError(FileActivityError):
    pass


class NotifyError(FileActivityError):
    pass


class MatrixAPIError(FileActivityError):
    pass
