"""Файл ключей проекта (dotenv): поиск, разбор, построчная правка, атомарная запись.

Значения в сообщения об ошибках не попадают: только имена, номера строк и пути.
"""

import os
import re
import subprocess
import sys
import tempfile

FILENAME = ".env"
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LINE_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<export>export[ \t]+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<rest>.*)$")
COMMENT_RE = re.compile(r"(?:^|[ \t])#")
PUBLIC_MARK = "# public"
FORBIDDEN = {"\n": "перевод строки", "\r": "возврат каретки", "#": "знак #", "$": "знак $"}


class EnvError(Exception):
    """Ошибка использования или данных: код выхода 2 (если не задан иной)."""

    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def fail(prog, message, code=2):
    print(f"{prog}: {message}", file=sys.stderr)
    sys.exit(code)


def split_opts(argv, valued=(), boolean=()):
    """Ручной разбор флагов: значения позиционных не попадают в сообщения об ошибках."""
    opts, rest, i = {}, [], 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            rest.extend(argv[i + 1:])
            break
        if arg in valued:
            if i + 1 >= len(argv):
                raise EnvError(f"у флага {arg} нет значения")
            opts[arg] = argv[i + 1]
            i += 2
            continue
        if arg in boolean:
            opts[arg] = True
        elif arg.startswith("-") and len(arg) > 1:
            raise EnvError("неизвестный флаг")
        else:
            rest.append(arg)
        i += 1
    return opts, rest


def _git(directory, *args):
    return subprocess.run(
        ["git", "-C", directory, *args], capture_output=True, text=True
    )


def find_file(directory):
    """Ближайший файл от DIR вверх до корня git (вне git - только DIR).

    Возвращает (путь или None, список проверенных путей).
    """
    if not os.path.isdir(directory):
        raise EnvError(f"каталога нет: {directory}")
    start = os.path.realpath(directory)
    top = _git(start, "rev-parse", "--show-toplevel")
    root = os.path.realpath(top.stdout.strip()) if top.returncode == 0 and top.stdout.strip() else start
    searched, current = [], start
    while True:
        candidate = os.path.join(current, FILENAME)
        searched.append(candidate)
        if os.path.isfile(candidate):
            return candidate, searched
        if current == root or os.path.dirname(current) == current:
            return None, searched
        current = os.path.dirname(current)


def check_value(value):
    """Значение, которое make и compose прочтут так же, как записано. Иначе EnvError."""
    if value == "":
        raise EnvError("значение пустое")
    for char, label in FORBIDDEN.items():
        if char in value:
            raise EnvError(f"значение не подходит для файла: {label}")
    if value != value.strip():
        raise EnvError("значение не подходит для файла: пробел по краю")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        raise EnvError("значение не подходит для файла: внешняя пара кавычек")


def parse_value(rest):
    comment = COMMENT_RE.search(rest)
    if comment:
        rest = rest[:comment.start()]
    value = rest.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


class Entry:
    """Строка файла: сырой текст с окончанием и, для переменной, разобранные части."""

    def __init__(self, raw):
        self.raw = raw
        self.name = None
        self.value = None
        self.indent = ""
        self.export = ""
        body = raw.rstrip("\n")
        self.eol = raw[len(body):]
        if body.endswith("\r"):
            body = body[:-1]
            self.eol = "\r" + self.eol
        self.body = body
        self.is_mark = body == PUBLIC_MARK

    @property
    def is_var(self):
        return self.name is not None

    def render(self, name, value):
        return f"{self.indent}{self.export}{name}={value}{self.eol}"


def _split_lines(text):
    return re.findall(r"[^\n]*\n|[^\n]+", text)


class Document:
    """Файл целиком. Правки меняют только нужные строки, остальные - байт в байт."""

    def __init__(self, path, text="", exists=True):
        self.target = os.path.realpath(path)
        self.exists = exists
        self.entries = [Entry(raw) for raw in _split_lines(text)]
        for number, entry in enumerate(self.entries, start=1):
            stripped = entry.body.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = LINE_RE.match(entry.body)
            if not match:
                raise EnvError(f"строка {number} не вида ИМЯ=значение")
            entry.name = match.group("name")
            entry.indent = match.group("indent")
            entry.export = match.group("export") or ""
            entry.value = parse_value(match.group("rest"))

    @classmethod
    def load(cls, path):
        real = os.path.realpath(path)
        try:
            with open(real, "rb") as handle:
                text = handle.read().decode("utf-8", "surrogateescape")
        except OSError as exc:
            raise EnvError(f"не удалось прочитать {path}: {exc.strerror}") from None
        return cls(real, text)

    @classmethod
    def new(cls, path):
        return cls(path, "", exists=False)

    def is_public(self, index):
        return index > 0 and self.entries[index - 1].is_mark

    def vars(self):
        return [(i, e) for i, e in enumerate(self.entries) if e.is_var]

    def effective(self):
        """Имя -> (значение последней строки, публична ли она), порядок первого появления."""
        result = {}
        for index, entry in self.vars():
            result[entry.name] = (entry.value, self.is_public(index))
        return result

    def secret_values(self):
        """Пары (значение, имя) всех секретных строк, включая дубли имени."""
        return [(e.value, e.name) for i, e in self.vars() if not self.is_public(i) and e.value]

    def _lookup(self, name):
        found = [(i, e) for i, e in self.vars() if e.name == name]
        if len(found) > 1:
            lines = ", ".join(str(i + 1) for i, _ in found)
            raise EnvError(f"{name} в строках {lines}")
        return found[0] if found else None

    def _eol(self):
        for entry in reversed(self.entries):
            if entry.eol:
                return entry.eol
        return "\n"

    def set(self, name, value, public):
        if not NAME_RE.match(name):
            raise EnvError("имя не вида [A-Za-z_][A-Za-z0-9_]*")
        check_value(value)
        found = self._lookup(name)
        if found:
            index, entry = found
            entry.raw = entry.render(name, value)
            entry.value = value
            marked = self.is_public(index)
            if marked and not public:
                del self.entries[index - 1]
            elif public and not marked:
                self.entries.insert(index, Entry(PUBLIC_MARK + self._eol()))
            return
        eol = self._eol()
        if self.entries:
            last = self.entries[-1]
            if not last.eol:
                last.raw += eol
                last.eol = eol
            if last.is_mark:
                self.entries.append(Entry(eol))
        if public:
            self.entries.append(Entry(PUBLIC_MARK + eol))
        line = Entry(f"{name}={value}{eol}")
        line.name, line.value = name, value
        self.entries.append(line)

    def delete(self, name):
        found = self._lookup(name)
        if not found:
            raise EnvError(f"{name} в файле нет")
        index, _ = found
        start = index - 1 if self.is_public(index) else index
        del self.entries[start:index + 1]

    def rename(self, old, new):
        if not NAME_RE.match(new):
            raise EnvError("новое имя не вида [A-Za-z_][A-Za-z0-9_]*")
        found = self._lookup(old)
        if not found:
            raise EnvError(f"{old} в файле нет")
        if self._lookup(new):
            raise EnvError(f"{new} уже есть в файле")
        _, entry = found
        entry.raw = self._renamed(entry, new)
        entry.name = new

    @staticmethod
    def _renamed(entry, new):
        # меняется только имя, значение и хвост строки остаются как были
        prefix = entry.indent + entry.export
        old_len = len(prefix) + len(entry.name)
        return prefix + new + entry.raw[old_len:]

    def text(self):
        return "".join(entry.raw for entry in self.entries)

    def save(self):
        directory = os.path.dirname(self.target)
        handle, temp = tempfile.mkstemp(dir=directory, prefix=".keys-")
        try:
            os.fchmod(handle, 0o600)
            with os.fdopen(handle, "wb") as out:
                out.write(self.text().encode("utf-8", "surrogateescape"))
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.target)
        except OSError as exc:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise EnvError(f"запись {self.target} не удалась: {exc.strerror}", code=1) from None


def open_for_edit(directory, create):
    """Файл ключей для правки. create (keyset): файл должен игнорироваться git, иначе секрет уйдёт в push."""
    path, searched = find_file(directory)
    if not path and not create:
        raise EnvError("файла нет, искали: " + ", ".join(searched))
    target = path or os.path.join(os.path.realpath(directory), FILENAME)
    if create:
        result = _git(os.path.dirname(target), "check-ignore", "-q", target)
        if result.returncode == 1:
            raise EnvError(f"{target} не в gitignore, писать секрет небезопасно")
    return Document.load(path) if path else Document.new(target)
