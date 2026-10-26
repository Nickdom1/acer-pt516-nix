#!/usr/bin/env python3
"""Compile the actual acer-wmi timeout functions with a mocked WMI transport.

A second test compiles the attribute's is_visible callback with mocked
quirks and WMI GUID lookup. No hardware or sysfs is accessed. The source defaults to the sibling
linux-pdx86 checkout; pass --source or set ACER_WMI_SOURCE to test another file.
Run with gcc available, for example:
  nix-shell -p gcc --run 'python3 maintainers/kernel-tests/test_acer_wmi_timeout.py'
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

DEFAULT_SOURCE = Path(__file__).resolve().parents[2] / "linux-pdx86/drivers/platform/x86/acer-wmi.c"
SOURCE = Path(os.environ.get("ACER_WMI_SOURCE", str(DEFAULT_SOURCE)))

pre = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
typedef uint8_t u8; typedef uint32_t u32; typedef uint64_t u64;
typedef int acpi_status;
typedef unsigned short umode_t;
#define BIT(n) (1U << (n))
#define GENMASK_ULL(h,l) ((UINT64_MAX << (l)) & (UINT64_MAX >> (63-(h))))
#define FIELD_GET(mask,value) (((value) & (mask)) >> __builtin_ctzll(mask))
#define ACPI_ALLOCATE_BUFFER 0
#define ACPI_FAILURE(status) ((status) != 0)
#define ACPI_TYPE_BUFFER 1
#define ACPI_TYPE_INTEGER 2
#define WMID_GUID3 "61EF69EA-865C-4BC3-A502-A0DEBA0CB531"
struct acpi_buffer { size_t length; void *pointer; };
union acpi_object {
 int type;
 struct { int type; size_t length; u8 *pointer; } buffer;
 struct { int type; u64 value; } integer;
};
struct device { int unused; }; struct kobject { int unused; };
struct attribute { const char *name; umode_t mode; };
struct device_attribute {
 struct attribute attr;
 ssize_t (*show)(struct device *, struct device_attribute *, char *);
 ssize_t (*store)(struct device *, struct device_attribute *, const char *, size_t);
};
static struct { u8 kbd_backlight_hotkey; } quirk = {0x84}, *quirks = &quirk;
static int acer_kbd_backlight_lock;
static int locked;
static void mutex_lock(int *lock) { (void)lock; assert(!locked); locked = 1; }
static void mutex_unlock(int *lock) { (void)lock; assert(locked); locked = 0; }
static u64 get_unaligned_le64(u8 *p) { u64 v=0; for (int i=0;i<8;i++) v |= (u64)p[i] << (8*i); return v; }
static u32 get_unaligned_le32(u8 *p) { u32 v=0; for (int i=0;i<4;i++) v |= (u32)p[i] << (8*i); return v; }
#define kfree free
static int kstrtou8(const char *s, int base, u8 *out) {
 char *end; unsigned long v;
 if (*s == '-') return -EINVAL;
 errno = 0; v = strtoul(s, &end, base);
 if (end == s) return -EINVAL;
 if (*end == '\n') end++;
 if (*end) return -EINVAL;
 if (errno || v > 255) return -ERANGE;
 *out = v; return 0;
}
static int sysfs_emit(char *s, const char *fmt, ...) {
 va_list ap; va_start(ap,fmt); int n = vsnprintf(s,64,fmt,ap); va_end(ap); return n;
}
static u8 reply[8];
static u8 sent[8];
static size_t read_len, write_len;
static int read_type, write_type, transport_error, calls, writes;
static u64 initial, write_result;
static int wmi_evaluate_method(const char *guid, int instance, u32 method,
                              struct acpi_buffer *input, struct acpi_buffer *output) {
 assert(!strcmp(guid, WMID_GUID3)); assert(!instance); assert(locked);
 bool set = method == 1;
 assert(method == 1 || method == 2);
 assert(input->length == (set ? 8U : 4U));
 u8 *p = input->pointer;
 assert(p[0] == (set ? 2 : 1)); assert(p[1] == 0x84); assert(p[2] == 8); assert(!p[3]);
 calls++;
 if (set) { writes++; memcpy(sent,p,8); assert(!p[6] && !p[7]); }
 if (transport_error) return 1;
 int type = set ? write_type : read_type;
 if (!type) { output->pointer = NULL; return 0; }
 union acpi_object *obj = calloc(1, sizeof(*obj)); assert(obj);
 obj->type = type;
 if (type == ACPI_TYPE_INTEGER) obj->integer.value = set ? write_result : initial;
 else {
   obj->buffer.length = set ? write_len : read_len;
   obj->buffer.pointer = reply;
   if (set) for (int i=0;i<8;i++) reply[i] = write_result >> (8*i);
   else for (int i=0;i<8;i++) reply[i] = initial >> (8*i);
 }
 output->pointer = obj; return 0;
}
static void reset(void) {
 initial = (30ULL<<40) | (75ULL<<32) | 0x80000;
 read_len=8; write_len=4; read_type=write_type=ACPI_TYPE_BUFFER;
 transport_error=calls=writes=0; write_result=0; memset(sent,0,sizeof(sent)); assert(!locked);
}
'''
post = r'''
int main(void) {
 char output[64];
 reset(); assert(kbd_backlight_timeout_show(NULL,NULL,output)==3);
 assert(!strcmp(output,"30\n")); assert(calls==1&&!writes);
 reset(); assert(kbd_backlight_timeout_store(NULL,NULL,"0\n",2)==2);
 assert(calls==2&&writes==1&&sent[4]==75&&sent[5]==0);
 reset(); assert(kbd_backlight_timeout_store(NULL,NULL,"255\n",4)==4); assert(sent[4]==75&&sent[5]==255);
 const char *bad[] = {"256", "-1", "garbage", "30 1"};
 for (unsigned int i=0;i<sizeof(bad)/sizeof(*bad);i++) {
  reset(); assert(kbd_backlight_timeout_store(NULL,NULL,bad[i],strlen(bad[i]))<0); assert(!calls);
 }
 reset(); read_len=4; assert(kbd_backlight_timeout_store(NULL,NULL,"0",1)==-ENOMSG); assert(!writes);
 reset(); read_type=0; assert(kbd_backlight_timeout_store(NULL,NULL,"0",1)==-ENOMSG); assert(!writes);
 reset(); read_type=3; assert(kbd_backlight_timeout_store(NULL,NULL,"0",1)==-ENOMSG); assert(!writes);
 reset(); initial|=1; assert(kbd_backlight_timeout_store(NULL,NULL,"0",1)==-EIO); assert(!writes);
 reset(); initial|=0x100; assert(kbd_backlight_timeout_store(NULL,NULL,"0",1)==-EIO); assert(!writes);
 reset(); transport_error=1; assert(kbd_backlight_timeout_show(NULL,NULL,output)==-EIO);
 reset(); write_len=3; assert(kbd_backlight_timeout_store(NULL,NULL,"0",1)==-ENOMSG);
 reset(); write_type=0; assert(kbd_backlight_timeout_store(NULL,NULL,"0",1)==-ENOMSG);
 reset(); write_result=1; assert(kbd_backlight_timeout_store(NULL,NULL,"0",1)==-EIO);
 reset(); write_result=0x100; assert(kbd_backlight_timeout_store(NULL,NULL,"0",1)==-EIO);
 reset(); read_type=write_type=ACPI_TYPE_INTEGER;
 assert(kbd_backlight_timeout_store(NULL,NULL,"0",1)==1); assert(sent[4]==75&&sent[5]==0);
 assert(!locked);
 puts("PASS: 18 timeout cases (real driver C, mocked WMI; no hardware calls)");
}
'''
# Visibility: the attribute group's is_visible callback, with the quirk's
# hotkey and the WMID_GUID3 lookup mocked.
visibility_pre = r'''
static int guid3_present, guid_queries;
static bool __attribute__((unused)) wmi_has_guid(const char *guid) {
 assert(!strcmp(guid, WMID_GUID3)); guid_queries++; return guid3_present;
}
struct attribute_group { struct attribute **attrs; umode_t (*is_visible)(struct kobject *, struct attribute *, int); };
#define DEVICE_ATTR_RW(_name) struct device_attribute dev_attr_##_name = \
 { .attr = { .name = #_name, .mode = 0644 }, .show = _name##_show, .store = _name##_store }
#define __ATTRIBUTE_GROUPS(_name) static const struct attribute_group *_name##_groups[] = { &_name##_group, NULL }
'''
visibility_post = r'''
int main(void) {
 reset();
 const struct attribute_group *group = acer_platform_groups[0];
 struct attribute *timeout = &dev_attr_kbd_backlight_timeout.attr, other = { "other", 0444 };
 assert(!acer_platform_groups[1] && group->is_visible && group->attrs[0] == timeout && !group->attrs[1]);
 /* the quirk provides the hotkey and the firmware has WMID_GUID3 */
 quirk.kbd_backlight_hotkey = 0x84; guid3_present = 1; guid_queries = 0;
 assert(group->is_visible(NULL, timeout, 0) == 0644); assert(guid_queries == 1);
 /* a quirk without a hotkey (every other model): hidden, no GUID lookup */
 quirk.kbd_backlight_hotkey = 0; guid_queries = 0;
 assert(group->is_visible(NULL, timeout, 0) == 0); assert(!guid_queries);
 /* the quirk's hotkey but no WMID_GUID3: hidden */
 quirk.kbd_backlight_hotkey = 0x84; guid3_present = 0;
 assert(group->is_visible(NULL, timeout, 0) == 0);
 /* the callback hides only this attribute */
 quirk.kbd_backlight_hotkey = 0;
 assert(group->is_visible(NULL, &other, 1) == 0444);
 assert(!calls && !locked);
 puts("PASS: 4 visibility cases (real driver C, mocked quirk and GUID lookup; no hardware calls)");
}
'''
# The development tree adds the attribute at probe instead of through
# .dev_groups; it has no is_visible callback to test.
LEGACY_REGISTRATION = "devm_device_add_group(&device->dev, &acer_kbd_backlight_group)"


class AcerWmiTimeoutTests(unittest.TestCase):
    def prerequisites(self):
        compiler = shutil.which("gcc")
        if compiler is None:
            if os.environ.get('TURBO_REQUIRE_GCC') == '1':
                self.fail('gcc is required for acceptance')
            self.skipTest("gcc is required for the mocked firmware C test")
        if not SOURCE.is_file():
            if os.environ.get('TURBO_REQUIRE_GCC') == '1':
                self.fail(f'kernel source is required for acceptance: {SOURCE}')
            self.skipTest(f"kernel source is missing: {SOURCE}; set ACER_WMI_SOURCE")
        source = SOURCE.read_text()
        start = source.index("static int acer_kbd_backlight_timeout(")
        # The driver may name its WMID_GUID3 method ids, function numbers and
        # reply masks; carry those definitions over with the function text.
        defines = "".join(
            line + "\n"
            for line in source.splitlines()
            if line.startswith("#define ACER_WMID3_")
        )
        return compiler, source, start, defines

    def compile_and_run(self, compiler, program):
        with tempfile.TemporaryDirectory(prefix="predator-timeout-unit-") as directory:
            path = Path(directory)
            (path / "test.c").write_text(program)
            subprocess.run(
                [compiler, "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                 "-std=gnu11", str(path / "test.c"), "-o", str(path / "test")],
                check=True,
            )
            subprocess.run([str(path / "test")], check=True)

    def test_actual_timeout_functions_with_mocked_firmware(self):
        compiler, source, start, defines = self.prerequisites()
        end = source.index("static DEVICE_ATTR_RW(kbd_backlight_timeout);")
        self.compile_and_run(compiler, pre + defines + source[start:end] + post)

    def test_attribute_visibility_with_mocked_quirks(self):
        compiler, source, start, defines = self.prerequisites()
        marker = "__ATTRIBUTE_GROUPS(acer_platform);"
        end = source.find(marker)
        if end < 0:
            if LEGACY_REGISTRATION in source:
                self.skipTest("source adds the attribute at probe; no is_visible callback")
            self.fail("no acer_platform attribute groups and no probe-time registration found")
        code = defines + source[start:end + len(marker)]
        self.compile_and_run(compiler, pre + visibility_pre + code + visibility_post)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source", type=Path, default=SOURCE)
    args, remaining = parser.parse_known_args()
    SOURCE = args.source
    unittest.main(argv=[sys.argv[0], *remaining])
