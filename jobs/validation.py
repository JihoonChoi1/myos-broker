"""Upload checks, split by what they protect.

check_size protects the broker: mkfs never checks user program sizes, and a
file over 48 direct blocks overflows the SimpleFS inode. It always runs.

check_elf_header only protects the user from a pointless run. MyOS's own
ELF loader rejects bad files too (and producing that rejection is a useful
test), so callers may skip it.
"""
import struct

from django.conf import settings

ELF_MAGIC = b"\x7fELF"
ELFCLASS32 = 1
ELFDATA2LSB = 1
EM_386 = 3
ELF32_EHDR_SIZE = 52


class UploadError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def check_size(size: int) -> None:
    limit = settings.MAX_BINARY_BYTES
    if size == 0:
        raise UploadError("empty_file", "uploaded file is empty")
    if size > limit:
        raise UploadError(
            "file_too_large",
            f"binary is {size} bytes; MyOS SimpleFS holds at most {limit} bytes "
            f"(48 blocks x 512) per file",
        )


def check_elf_header(head: bytes) -> None:
    """Mirror what MyOS's elf_load() needs: ELF magic, 32-bit little-endian,
    i386."""
    if len(head) < ELF32_EHDR_SIZE:
        raise UploadError("not_elf", f"file is {len(head)} bytes, shorter than an ELF32 header")
    if head[:4] != ELF_MAGIC:
        raise UploadError("not_elf", "missing ELF magic (\\x7fELF)")
    if head[4] != ELFCLASS32 or head[5] != ELFDATA2LSB:
        raise UploadError("wrong_elf_class", "MyOS runs 32-bit little-endian ELF only")
    (machine,) = struct.unpack_from("<H", head, 18)
    if machine != EM_386:
        raise UploadError("wrong_arch", f"e_machine is {machine}, MyOS needs i386 ({EM_386})")
