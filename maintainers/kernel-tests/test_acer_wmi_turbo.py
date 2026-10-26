#!/usr/bin/env python3
"""Exercise the driver's Turbo transactions with mocked WMI and real threads.

A second test compiles the driver's gaming WMI reply handling and turbo LED
helpers against mocked ACPI replies. No hardware is accessed. ACER_WMI_SOURCE
may select another driver revision.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(os.environ.get(
    "ACER_WMI_SOURCE",
    str(Path(__file__).resolve().parents[2] / "linux-pdx86/drivers/platform/x86/acer-wmi.c"),
))

PREAMBLE = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef uint8_t u8;
struct device { int references; };
struct platform_device { struct device dev; };
struct platform_profile_ops { int unused; };
static const struct platform_profile_ops acer_predator_v4_platform_profile_ops;
#define ERR_PTR(error) ((void *)(long)(error))
#define PTR_ERR(pointer) ((long)(pointer))
#define IS_ERR(pointer) ((unsigned long)(pointer) >= (unsigned long)-4095)
enum platform_profile_option {
    PLATFORM_PROFILE_PERFORMANCE, PLATFORM_PROFILE_BALANCED_PERFORMANCE,
    PLATFORM_PROFILE_BALANCED, PLATFORM_PROFILE_QUIET, PLATFORM_PROFILE_LOW_POWER
};
#define ACER_PREDATOR_V4_THERMAL_PROFILE_TURBO 5
#define ACER_PREDATOR_V4_THERMAL_PROFILE_PERFORMANCE 4
#define ACER_PREDATOR_V4_THERMAL_PROFILE_BALANCED 1
#define ACER_PREDATOR_V4_THERMAL_PROFILE_QUIET 0
#define ACER_PREDATOR_V4_THERMAL_PROFILE_ECO 6
#define ACER_WMID_MISC_SETTING_PLATFORM_PROFILE 0x0b
#define ACER_CAP_PLATFORM_PROFILE 1
#define ACER_CAP_TURBO_LED 2
#define IS_ERR_OR_NULL(p) (!(p))
static struct device device, *platform_profile_device = &device;
static struct platform_device pdev;
/* Trees based on for-next still set this in acer_platform_profile_setup(). */
static bool platform_profile_support __attribute__((unused));
static struct device *register_result = &device;
static int registrations;
static struct device *devm_platform_profile_register(struct device *parent, const char *name,
        void *drvdata, const struct platform_profile_ops *ops) {
    assert(parent == &pdev.dev && ops == &acer_predator_v4_platform_profile_ops);
    registrations++;
    return register_result;
}
static struct { bool turbo_profile, no_eco_profile, platform_profile; }
    quirk = {true, true, true}, *quirks = &quirk;
static bool turbo_key_profile, fan_turbo, turbo_led, cycle_gaming_thermal_profile;
static int current_profile = 1, last_non_turbo_profile = 1;
static int fail_read, fail_write, fail_latch, notifications, fan_writes;
static bool balance_on_notify, unbind_on_notify;
static pthread_mutex_t acer_turbo_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t sync_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t sync_cond = PTHREAD_COND_INITIALIZER;
static bool pause_fans, fan_waiting, release_fans, writer_attempted;
static _Thread_local bool writer, holds_turbo_lock;
static pthread_mutex_t profile_lock = PTHREAD_MUTEX_INITIALIZER;
static _Thread_local bool holds_profile_lock;
static void mutex_lock(pthread_mutex_t *lock) {
    if (writer) {
        assert(!pthread_mutex_lock(&sync_lock));
        writer_attempted = true;
        assert(!pthread_cond_broadcast(&sync_cond));
        assert(!pthread_mutex_unlock(&sync_lock));
    }
    assert(!pthread_mutex_lock(lock));
    holds_turbo_lock = true;
}
static void mutex_unlock(pthread_mutex_t *lock) {
    holds_turbo_lock = false;
    assert(!pthread_mutex_unlock(lock));
}
static int caps = ~0;
static int has_cap(int cap) { return !!(caps & cap); }
/* The profile core rejects choices the probe callback did not advertise. */
static unsigned long choices;
static void set_bit(int bit, unsigned long *mask) { *mask |= 1UL << bit; }
static int WMID_gaming_get_misc_setting(int index, u8 *out) {
    assert(index == 0x0b);
    if (fail_read) return -EIO;
    *out = current_profile;
    return 0;
}
static int WMID_gaming_set_misc_setting(int index, int value) {
    assert(index == 0x0b);
    if (fail_write) return -EIO;
    current_profile = value;
    return 0;
}
static void acer_set_turbo(bool on) {
    assert(!pthread_mutex_lock(&sync_lock));
    if (pause_fans && on) {
        fan_waiting = true;
        assert(!pthread_cond_broadcast(&sync_cond));
        while (!release_fans) assert(!pthread_cond_wait(&sync_cond, &sync_lock));
    }
    assert(!pthread_mutex_unlock(&sync_lock));
    fan_turbo = turbo_led = on;
    fan_writes++;
}
static int acer_get_turbo_led(bool *on) {
    if (fail_latch) return -EIO;
    *on = turbo_led; return 0;
}
static struct device *get_device(struct device *dev) { dev->references++; return dev; }
static void put_device(struct device *dev) { assert(dev->references > 0); dev->references--; }
static int acer_predator_v4_platform_profile_get(struct device *, enum platform_profile_option *);
static int acer_predator_v4_platform_profile_set(struct device *, enum platform_profile_option);
static int core_get(struct device *dev, enum platform_profile_option *profile) {
    assert(!holds_profile_lock);
    assert(!pthread_mutex_lock(&profile_lock)); holds_profile_lock = true;
    int result = acer_predator_v4_platform_profile_get(dev, profile);
    holds_profile_lock = false; assert(!pthread_mutex_unlock(&profile_lock));
    return result;
}
static int core_set(struct device *dev, enum platform_profile_option profile) {
    assert(!holds_profile_lock);
    assert(!pthread_mutex_lock(&profile_lock)); holds_profile_lock = true;
    int result = (choices & (1UL << profile)) ? acer_predator_v4_platform_profile_set(dev, profile) : -EOPNOTSUPP;
    holds_profile_lock = false; assert(!pthread_mutex_unlock(&profile_lock));
    return result;
}
static void platform_profile_notify(struct device *dev) {
    /* Core notification may run a profile reader/writer immediately. */
    assert(!holds_turbo_lock);
    assert(!holds_profile_lock);
    enum platform_profile_option observed;
    assert(!core_get(dev, &observed));
    assert(dev == &device);
    notifications++;
    if (unbind_on_notify) {
        assert(dev->references > 0);
        mutex_lock(&acer_turbo_lock);
        platform_profile_device = NULL;
        mutex_unlock(&acer_turbo_lock);
    }
    if (balance_on_notify)
        assert(!core_set(dev, PLATFORM_PROFILE_BALANCED));
}
static int platform_profile_cycle(void) {
    enum platform_profile_option profile;
    assert(!holds_turbo_lock);
    assert(!core_get(&device, &profile));
    return core_set(&device, PLATFORM_PROFILE_PERFORMANCE);
}
'''

CASE_SUPPORT = r'''
static void reset(void) {
    assert(!device.references);
    assert(!pthread_mutex_trylock(&acer_turbo_lock));
    assert(!pthread_mutex_unlock(&acer_turbo_lock));
    platform_profile_device = &device;
    quirk.turbo_profile = quirk.no_eco_profile = quirk.platform_profile = true;
    current_profile = last_non_turbo_profile = 1;
    turbo_key_profile = fan_turbo = turbo_led = cycle_gaming_thermal_profile = false;
    balance_on_notify = unbind_on_notify = false;
    fail_read = fail_write = fail_latch = notifications = fan_writes = 0;
    caps = ~0;
    choices = 0;
    register_result = &device;
    registrations = 0;
    assert(!acer_predator_v4_platform_profile_probe(NULL, &choices));
    pause_fans = fan_waiting = release_fans = writer_attempted = false;
}
static void *press_key(void *unused) { (void)unused; assert(!acer_toggle_turbo()); return NULL; }
static void *set_balanced(void *unused) {
    (void)unused;
    writer = true;
    assert(!core_set(&device, PLATFORM_PROFILE_BALANCED));
    return NULL;
}
'''

CASES = r'''
int main(void) {
    pthread_t key_thread, writer_thread;
    enum platform_profile_option profile;
    reset();
    assert(!acer_toggle_turbo());
    assert(current_profile == 5 && fan_turbo && turbo_key_profile);
    assert(!acer_toggle_turbo());
    assert(current_profile == 1 && !fan_turbo && !turbo_key_profile);
    assert(notifications == 2);

    /* Firmware can retain the Turbo latch across a warm boot. */
    reset(); current_profile = 5; fan_turbo = turbo_led = true;
    assert(!core_set(&device, PLATFORM_PROFILE_BALANCED));
    assert(current_profile == 1 && !fan_turbo && !turbo_led);
    assert(!acer_toggle_turbo());
    assert(current_profile == 5 && fan_turbo && turbo_key_profile);

    reset(); current_profile = 5; fail_latch = true;
    assert(core_set(&device, PLATFORM_PROFILE_QUIET) == -EIO);
    assert(current_profile == 5 && last_non_turbo_profile == 1);
    assert(!fan_writes && !turbo_key_profile);
    fail_latch = false;
    assert(!core_set(&device, PLATFORM_PROFILE_BALANCED));

    reset(); quirk.turbo_profile = false; fail_latch = true;
    assert(!core_set(&device, PLATFORM_PROFILE_BALANCED));
    assert(!fan_writes);

    /* Without the LED capability (force_caps) the latch is never read. */
    reset(); caps &= ~ACER_CAP_TURBO_LED; fail_latch = true; current_profile = 5;
    assert(!core_set(&device, PLATFORM_PROFILE_BALANCED));
    assert(current_profile == 1 && !fan_writes && !turbo_key_profile);

    reset(); balance_on_notify = true;
    assert(!acer_toggle_turbo());
    assert(current_profile == 1 && !fan_turbo && !turbo_key_profile);

    /* Pause between the key's profile and fan writes, then start sysfs. */
    reset(); pause_fans = true;
    assert(!pthread_create(&key_thread, NULL, press_key, NULL));
    assert(!pthread_mutex_lock(&sync_lock));
    while (!fan_waiting) assert(!pthread_cond_wait(&sync_cond, &sync_lock));
    assert(!pthread_mutex_unlock(&sync_lock));
    assert(pthread_mutex_trylock(&acer_turbo_lock) == EBUSY);
    assert(!pthread_create(&writer_thread, NULL, set_balanced, NULL));
    assert(!pthread_mutex_lock(&sync_lock));
    while (!writer_attempted) assert(!pthread_cond_wait(&sync_cond, &sync_lock));
    release_fans = true;
    assert(!pthread_cond_broadcast(&sync_cond));
    assert(!pthread_mutex_unlock(&sync_lock));
    assert(!pthread_join(key_thread, NULL));
    assert(!pthread_join(writer_thread, NULL));
    assert(current_profile == 1 && !fan_turbo && !turbo_key_profile);

    reset(); current_profile = 0;
    assert(!acer_toggle_turbo()); assert(last_non_turbo_profile == 0);
    assert(!acer_toggle_turbo()); assert(current_profile == 0);

    reset(); fail_write = true;
    assert(acer_toggle_turbo() == -EIO);
    assert(!fan_writes && !notifications && !turbo_key_profile && current_profile == 1);
    reset(); fail_read = true;
    assert(acer_toggle_turbo() == -EIO);
    assert(!fan_writes && !notifications);

    reset(); platform_profile_device = NULL;
    assert(acer_toggle_turbo() == -ENODEV);
    assert(!fan_writes && !notifications);
    assert(acer_thermal_profile_change() == -ENODEV);
    cycle_gaming_thermal_profile = true;
    assert(acer_thermal_profile_change() == -ENODEV);

    reset(); quirk.turbo_profile = false; platform_profile_device = NULL;
    assert(!acer_toggle_turbo()); assert(fan_turbo && current_profile == 1);
    assert(!acer_toggle_turbo()); assert(!fan_turbo && !notifications);

    reset();
    assert(core_set(&device, PLATFORM_PROFILE_LOW_POWER) == -EOPNOTSUPP);
    assert(current_profile == 1);
    reset(); quirk.no_eco_profile = false; choices = 0;
    assert(!acer_predator_v4_platform_profile_probe(NULL, &choices));
    assert(!core_set(&device, PLATFORM_PROFILE_LOW_POWER));
    assert(current_profile == 6);
    assert(!core_set(&device, PLATFORM_PROFILE_PERFORMANCE));
    assert(current_profile == 5 && !fan_turbo && !turbo_key_profile);
    assert(!core_get(&device, &profile));
    assert(profile == PLATFORM_PROFILE_PERFORMANCE);

    reset(); cycle_gaming_thermal_profile = true;
    assert(!acer_thermal_profile_change());
    assert(current_profile == 5 && !fan_turbo && !turbo_key_profile);
    reset(); current_profile = 0;
    assert(!acer_thermal_profile_change()); assert(current_profile == 5);
    assert(!acer_thermal_profile_change()); assert(current_profile == 0);

    reset(); unbind_on_notify = true;
    assert(!acer_toggle_turbo());
    assert(!device.references && !platform_profile_device);

    puts("PASS: Turbo transaction, concurrent profile write, errors, quirks and lock ordering");
}
'''

# Cases that need the folded series A semantics (patch 5 returns an errno from the
# LED helper; patch 8 adopts a retained latch only after the profile write
# succeeded). Pre-fold trees adopt before the write and fail them by design.
FOLDED_CASES = r'''
int main(void) {
    /* A retained latch is not acted on when the profile write fails. */
    reset(); current_profile = 5; fan_turbo = turbo_led = true; fail_write = true;
    assert(core_set(&device, PLATFORM_PROFILE_BALANCED) == -EIO);
    assert(current_profile == 5 && last_non_turbo_profile == 1);
    assert(fan_turbo && turbo_led && !fan_writes && !turbo_key_profile);
    fail_write = false;
    assert(!core_set(&device, PLATFORM_PROFILE_BALANCED));
    assert(current_profile == 1 && !fan_turbo && !turbo_led && !turbo_key_profile);

    /* The profile device is published only after a successful registration. */
    reset(); platform_profile_device = NULL; register_result = ERR_PTR(-ENOMEM);
    assert(acer_platform_profile_setup(&pdev) == -ENOMEM);
    assert(!platform_profile_device && registrations == 1);
    assert(acer_thermal_profile_change() == -ENODEV && !notifications);
    register_result = &device;
    assert(!acer_platform_profile_setup(&pdev));
    assert(platform_profile_device == &device);
    reset(); quirk.platform_profile = false; platform_profile_device = NULL;
    assert(!acer_platform_profile_setup(&pdev));
    assert(!platform_profile_device && !registrations);

    /* The mode key holds a reference across a notification racing an unbind. */
    reset(); unbind_on_notify = true;
    assert(!acer_thermal_profile_change());
    assert(current_profile == 5 && notifications == 1);
    assert(!device.references && !platform_profile_device);
    puts("PASS: retained latch with a failed write, profile device publication, notify racing an unbind");
}
'''

HELPER_PREAMBLE = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef u32 acpi_status;
typedef u64 acpi_size;
#define AE_OK 0u
#define AE_ERROR 1u
#define AE_NOT_FOUND 5u
#define AE_BAD_PARAMETER 0x1001u
#define ACPI_FAILURE(status) ((status) != AE_OK)
#define ACPI_SUCCESS(status) ((status) == AE_OK)
#define ACPI_TYPE_INTEGER 1
#define ACPI_TYPE_STRING 2
#define ACPI_TYPE_BUFFER 3
#define ACPI_ALLOCATE_BUFFER ((acpi_size)-1)
#define EIO 5
#define BIT(n) (1UL << (n))
#define GENMASK_ULL(h, l) (((~0ULL) << (l)) & (~0ULL >> (63 - (h))))
#define FIELD_PREP(mask, value) (((u64)(value) << __builtin_ctzll(mask)) & (mask))
#define FIELD_GET(mask, value) (((u64)(value) & (mask)) >> __builtin_ctzll(mask))
union acpi_object {
    u32 type;
    struct { u32 type; u64 value; } integer;
    struct { u32 type; u32 length; u8 *pointer; } buffer;
};
struct acpi_buffer { acpi_size length; void *pointer; };
static u32 get_unaligned_le32(const void *pointer) {
    const u8 *b = pointer;
    return b[0] | b[1] << 8 | b[2] << 16 | (u32)b[3] << 24;
}
static u64 get_unaligned_le64(const void *pointer) {
    const u8 *b = pointer;
    return get_unaligned_le32(b) | (u64)get_unaligned_le32(b + 4) << 32;
}
/* The ACPI reply the next wmi_evaluate_method() call returns. */
enum reply_kind { REPLY_NONE, REPLY_INTEGER, REPLY_BUFFER, REPLY_STRING };
static enum reply_kind reply_kind;
static u8 reply_bytes[16];
static u32 reply_length;
static u64 reply_integer;
static acpi_status evaluate_status;
static u32 last_method;
static u64 last_input;
static int calls, live_objects;
struct wmi_interface { u32 capability; };
static struct wmi_interface iface, *interface = &iface;
struct quirk_entry { bool turbo_led_state_valid; };
static struct quirk_entry quirk, *quirks = &quirk;
static bool has_cap(u32 cap) { return interface->capability & cap; }
#define ACER_WMID_FAN_MODE_AUTO 0x01
#define ACER_WMID_FAN_MODE_TURBO 0x02
#define ACER_WMID_OC_NORMAL 0x00
#define ACER_WMID_OC_TURBO 0x02
#define ACER_WMID_MISC_SETTING_OC_1 0x05
#define ACER_WMID_MISC_SETTING_OC_2 0x07
static int fan_mode, fan_mode_writes, oc_writes, warnings;
static u8 oc_values[2];
static int WMID_gaming_set_fan_mode(u8 mode) { fan_mode = mode; fan_mode_writes++; return 0; }
static int WMID_gaming_set_misc_setting(u32 setting, u8 value) {
    assert(setting == ACER_WMID_MISC_SETTING_OC_1 || setting == ACER_WMID_MISC_SETTING_OC_2);
    oc_values[setting == ACER_WMID_MISC_SETTING_OC_2] = value;
    oc_writes++;
    return 0;
}
#define pr_warn(...) do { warnings++; } while (0)
"""

HELPER_MOCKS = r"""
static acpi_status wmi_evaluate_method(const char *guid, u8 instance, u32 method_id,
        const struct acpi_buffer *in, struct acpi_buffer *out) {
    union acpi_object *obj;
    assert(!strcmp(guid, WMID_GUID4) && instance == 0);
    assert(in->length == sizeof(u64));
    assert(out->length == ACPI_ALLOCATE_BUFFER && !out->pointer);
    calls++;
    last_method = method_id;
    memcpy(&last_input, in->pointer, sizeof(last_input));
    if (evaluate_status != AE_OK || reply_kind == REPLY_NONE)
        return evaluate_status;
    obj = calloc(1, sizeof(*obj) + sizeof(reply_bytes));
    assert(obj);
    live_objects++;
    if (reply_kind == REPLY_INTEGER) {
        obj->type = ACPI_TYPE_INTEGER;
        obj->integer.value = reply_integer;
    } else {
        obj->type = reply_kind == REPLY_STRING ? ACPI_TYPE_STRING : ACPI_TYPE_BUFFER;
        obj->buffer.length = reply_length;
        obj->buffer.pointer = (u8 *)(obj + 1);
        memcpy(obj->buffer.pointer, reply_bytes, reply_length);
    }
    out->pointer = obj;
    return AE_OK;
}
static void kfree(void *pointer) {
    if (pointer) {
        live_objects--;
        free(pointer);
    }
}
"""

HELPER_CASES = r"""
static void reply_buffer(const u8 *bytes, u32 length) {
    reply_kind = REPLY_BUFFER;
    memcpy(reply_bytes, bytes, length);
    reply_length = length;
}
static void reset(void) {
    assert(!live_objects);
    reply_kind = REPLY_NONE;
    memset(reply_bytes, 0, sizeof(reply_bytes));
    reply_length = 0;
    reply_integer = 0;
    evaluate_status = AE_OK;
    calls = 0;
    last_method = 0;
    last_input = 0;
    iface.capability = ACER_CAP_TURBO_LED | ACER_CAP_TURBO_FAN | ACER_CAP_TURBO_OC;
    quirk.turbo_led_state_valid = false;
    fan_mode = fan_mode_writes = oc_writes = warnings = 0;
    oc_values[0] = oc_values[1] = 0xff;
}
int main(void) {
    static const u8 bytes[12] = { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 };
    static const u8 status_ok[4] = { 0 }, status_rejected[4] = { 1 };
    static const u8 led_on[12] = { 0, 1 }, led_off[12] = { 0, 0, 0xff }, led_failed[12] = { 1, 1 };
    u64 out;
    bool on;

    /* Result-taking callers: missing, short, odd-sized and wrongly typed replies fail. */
    reset(); out = 0x55;
    assert(WMI_gaming_execute_u64(7, 0x1234, &out) == AE_ERROR && out == 0x55);
    assert(calls == 1 && last_method == 7 && last_input == 0x1234);
    for (u32 length = 0; length < 8; length++) {
        if (length == 4)
            continue;
        reset(); reply_buffer(bytes, length); out = 0x55;
        assert(ACPI_FAILURE(WMI_gaming_execute_u64(7, 0, &out)) && out == 0x55);
    }
    reset(); reply_kind = REPLY_STRING; reply_length = 8; out = 0x55;
    assert(ACPI_FAILURE(WMI_gaming_execute_u64(7, 0, &out)) && out == 0x55);
    reset(); evaluate_status = AE_NOT_FOUND; out = 0x55;
    assert(WMI_gaming_execute_u64(7, 0, &out) == AE_NOT_FOUND && out == 0x55);

    /* 4-byte replies stay accepted, integers pass through, larger buffers give 8 bytes. */
    reset(); reply_buffer(bytes, 4);
    assert(WMI_gaming_execute_u64(7, 0, &out) == AE_OK && out == 0x03020100);
    reset(); reply_kind = REPLY_INTEGER; reply_integer = 0x1122334455667788ULL;
    assert(WMI_gaming_execute_u64(7, 0, &out) == AE_OK && out == 0x1122334455667788ULL);
    reset(); reply_buffer(bytes, 8);
    assert(WMI_gaming_execute_u64(7, 0, &out) == AE_OK && out == 0x0706050403020100ULL);
    reset(); reply_buffer(bytes, 12);
    assert(WMI_gaming_execute_u64(7, 0, &out) == AE_OK && out == 0x0706050403020100ULL);

    /* Result-less setters keep ignoring the reply. */
    reset();
    assert(WMI_gaming_execute_u64(7, 0, NULL) == AE_OK);
    reset(); reply_buffer(bytes, 3);
    assert(WMI_gaming_execute_u64(7, 0, NULL) == AE_OK);
    reset(); reply_kind = REPLY_STRING; reply_length = 2;
    assert(WMI_gaming_execute_u64(7, 0, NULL) == AE_OK);

    /* Legacy turbo LED: 0/1 in byte 2, reply ignored, a non-zero result reads as on. */
    reset(); reply_kind = REPLY_INTEGER; reply_integer = 1;
    assert(!acer_set_turbo_led(true));
    assert(last_method == ACER_WMID_SET_GAMING_LED_METHODID && last_input == 0x10001);
    reset();
    assert(!acer_set_turbo_led(false) && last_input == 0x1);
    reset(); evaluate_status = AE_NOT_FOUND;
    assert(acer_set_turbo_led(true) == -EIO);
    reset(); iface.capability = 0;
    assert(acer_set_turbo_led(true) == -EIO && acer_get_turbo_led(&on) == -EIO && !calls);
    reset(); reply_kind = REPLY_INTEGER; reply_integer = 0x100;
    assert(!acer_get_turbo_led(&on) && on);
    assert(last_method == ACER_WMID_GET_GAMING_LED_METHODID && last_input == 1);
    reset(); reply_kind = REPLY_INTEGER; reply_integer = 0;
    assert(!acer_get_turbo_led(&on) && !on);
    reset(); reply_buffer(bytes, 3); on = true;
    assert(acer_get_turbo_led(&on) == -EIO && on);

    /* Flagged firmware: 0x10/0x11 in byte 2 and a checked status byte. */
    reset(); quirk.turbo_led_state_valid = true; reply_buffer(status_ok, 4);
    assert(!acer_set_turbo_led(true) && last_input == 0x110001);
    reset(); quirk.turbo_led_state_valid = true; reply_buffer(status_ok, 4);
    assert(!acer_set_turbo_led(false) && last_input == 0x100001);
    reset(); quirk.turbo_led_state_valid = true; reply_buffer(status_rejected, 4);
    assert(acer_set_turbo_led(true) == -EIO);
    reset(); quirk.turbo_led_state_valid = true;
    assert(acer_set_turbo_led(true) == -EIO);
    reset(); quirk.turbo_led_state_valid = true; reply_buffer(status_ok, 2);
    assert(acer_set_turbo_led(true) == -EIO);
    reset(); quirk.turbo_led_state_valid = true; reply_buffer(led_on, 12);
    assert(!acer_get_turbo_led(&on) && on);
    reset(); quirk.turbo_led_state_valid = true; reply_buffer(led_off, 12);
    assert(!acer_get_turbo_led(&on) && !on);
    reset(); quirk.turbo_led_state_valid = true; reply_buffer(led_failed, 12); on = false;
    assert(acer_get_turbo_led(&on) == -EIO && !on);
    reset(); quirk.turbo_led_state_valid = true; reply_buffer(led_on, 5);
    assert(acer_get_turbo_led(&on) == -EIO);

    /* A rejected LED write is reported while the fans and OC settings still switch. */
    reset(); quirk.turbo_led_state_valid = true; reply_buffer(status_rejected, 4);
    acer_set_turbo(true);
    assert(warnings == 1 && fan_mode_writes == 1 && fan_mode == ACER_WMID_FAN_MODE_TURBO);
    assert(oc_writes == 2 && oc_values[0] == ACER_WMID_OC_TURBO && oc_values[1] == ACER_WMID_OC_TURBO);
    reset(); quirk.turbo_led_state_valid = true; reply_buffer(status_ok, 4);
    acer_set_turbo(false);
    assert(!warnings && fan_mode == ACER_WMID_FAN_MODE_AUTO);
    assert(oc_values[0] == ACER_WMID_OC_NORMAL && oc_values[1] == ACER_WMID_OC_NORMAL);
    reset(); iface.capability = ACER_CAP_TURBO_LED;
    acer_set_turbo(true);
    assert(!fan_mode_writes && !oc_writes);
    reset();
    puts("PASS: gaming WMI replies, legacy and flagged turbo LED helpers, turbo switch");
}
"""

DRIVER_DEFINES = re.compile(
    r"^#define (?:ACER_CAP_\w+|ACER_WMID_(?:SET|GET)_GAMING_LED_METHODID|ACER_GAMING_LED_\w+|WMID_GUID4)\b.*$",
    re.MULTILINE,
)


def folded_series(source):
    """True for the folded series A (prep/linux-a and later), whose LED helper
    returns an errno; older development trees predate that fold."""
    return "static int acer_set_turbo_led(" in source


def extract_function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for position in range(opening, len(source)):
        depth += (source[position] == "{") - (source[position] == "}")
        if depth == 0:
            return source[start:position + 1]
    raise ValueError(f"Incomplete function: {signature}")


class AcerWmiTurboTests(unittest.TestCase):
    def transaction_sources(self):
        compiler = shutil.which("gcc")
        if compiler is None or not SOURCE.is_file():
            if os.environ.get('TURBO_REQUIRE_GCC') == '1':
                self.fail('gcc and ACER_WMI_SOURCE are required for acceptance')
            self.skipTest("gcc and ACER_WMI_SOURCE are required")
        source = SOURCE.read_text()
        signatures = (
            "static int acer_set_turbo_profile(",
            "static int acer_toggle_turbo(",
            "static int\nacer_predator_v4_platform_profile_get(",
            "static int\nacer_predator_v4_platform_profile_set(",
            "static int acer_thermal_profile_change(",
            "static int\nacer_predator_v4_platform_profile_probe(",
            "static int acer_platform_profile_setup(",
        )
        code = "\n".join(extract_function(source, signature) for signature in signatures)
        return compiler, source, code

    def test_actual_turbo_transactions(self):
        compiler, source, code = self.transaction_sources()
        compile_and_run(compiler, PREAMBLE + code + CASE_SUPPORT + CASES)

    def test_actual_folded_retained_latch_and_registration(self):
        compiler, source, code = self.transaction_sources()
        if not folded_series(source):
            self.skipTest("source predates the folded series A; "
                          "these cases apply to prep/linux-a and later")
        compile_and_run(compiler, PREAMBLE + code + CASE_SUPPORT + FOLDED_CASES)

    def test_actual_wmi_replies_and_led_helpers(self):
        compiler = shutil.which("gcc")
        if compiler is None or not SOURCE.is_file():
            if os.environ.get('TURBO_REQUIRE_GCC') == '1':
                self.fail('gcc and ACER_WMI_SOURCE are required for acceptance')
            self.skipTest("gcc and ACER_WMI_SOURCE are required")
        source = SOURCE.read_text()
        if not folded_series(source):
            self.skipTest("source predates the folded series A helper interface; "
                          "this case applies to prep/linux-a and later")
        defines = "\n".join(DRIVER_DEFINES.findall(source))
        signatures = (
            "static acpi_status\nWMI_gaming_execute_u64(",
            "static acpi_status WMID_gaming_set_u64(",
            "static acpi_status WMID_gaming_get_u64(",
            "static int acer_set_turbo_led(",
            "static int acer_get_turbo_led(",
            "static void acer_set_turbo(",
        )
        code = "\n".join(extract_function(source, signature) for signature in signatures)
        compile_and_run(compiler, HELPER_PREAMBLE + defines + "\n" + HELPER_MOCKS + code + HELPER_CASES)


def compile_and_run(compiler, text):
    with tempfile.TemporaryDirectory(prefix="predator-turbo-unit-") as directory:
        path = Path(directory)
        (path / "test.c").write_text(text)
        subprocess.run(
            [compiler, "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-Wno-unused-function",
             "-pthread", "-std=gnu11", str(path / "test.c"), "-o", str(path / "test")],
            check=True,
        )
        subprocess.run([str(path / "test")], check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
