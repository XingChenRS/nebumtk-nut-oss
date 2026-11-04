#include "wrapper_crypto.h"
#include "wrapper_protocol.h"
#include "vendor/monocypher/monocypher.h"

#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

#if defined(__linux__)
#include <linux/memfd.h>
#endif

#ifndef MFD_CLOEXEC
#define MFD_CLOEXEC 0x0001U
#endif
#ifndef MFD_ALLOW_SEALING
#define MFD_ALLOW_SEALING 0x0002U
#endif
#ifndef MFD_EXEC
#define MFD_EXEC 0x0010U
#endif
#ifndef O_DIRECTORY
#define O_DIRECTORY 00200000
#endif
#ifndef O_TMPFILE
#define O_TMPFILE (020000000 | O_DIRECTORY)
#endif
#ifndef AT_EMPTY_PATH
#define AT_EMPTY_PATH 0x1000
#endif
#ifndef F_ADD_SEALS
#define F_ADD_SEALS 1033
#endif
#ifndef F_SEAL_SEAL
#define F_SEAL_SEAL 0x0001
#endif
#ifndef F_SEAL_SHRINK
#define F_SEAL_SHRINK 0x0002
#endif
#ifndef F_SEAL_GROW
#define F_SEAL_GROW 0x0004
#endif
#ifndef F_SEAL_WRITE
#define F_SEAL_WRITE 0x0008
#endif

#define DEVICE_CHALLENGE_VERSION "NBCH1"
#define DEVICE_GRANT_VERSION "NBGR1"
#define DEVICE_CHALLENGE_NONCE_LEN 32U
#define DEVICE_KEY_LEN 32U
#define DEVICE_GRANT_CIPHERTEXT_LEN 48U
#define DEVICE_INPUT_MAX 640U
#define DEVICE_AAD_MAX 512U

extern const NebumtkWrapHeader g_nebumtk_header;
extern const uint8_t g_nebumtk_payload_id[];
extern const uint8_t g_nebumtk_ciphertext[];
extern const uint8_t g_nebumtk_tag[];
extern const size_t g_nebumtk_ciphertext_len;

static int memfd_create_portable(const char *name, unsigned int flags) {
#ifdef __NR_memfd_create
    return (int)syscall(__NR_memfd_create, name, flags);
#else
    errno = ENOSYS;
    return -1;
#endif
}

extern char **environ;

static int execveat_portable(int fd, char *const argv[]) {
#ifdef __NR_execveat
    return (int)syscall(__NR_execveat, fd, "", argv, environ, AT_EMPTY_PATH);
#else
    (void)fd;
    (void)argv;
    errno = ENOSYS;
    return -1;
#endif
}

static void print_usage(const char *argv0) {
    fprintf(stderr, "usage: %s -s - -m <preload|exec> -- <args...>\n", argv0 ? argv0 : "wrapper.so");
}

static int fail_stage(const char *stage, int error_number) {
    fprintf(stderr, "NBERR:%s:%d\n", stage ? stage : "unknown", error_number);
    return 126;
}

static int hex_value(char ch) {
    if (ch >= '0' && ch <= '9') {
        return ch - '0';
    }
    if (ch >= 'a' && ch <= 'f') {
        return 10 + (ch - 'a');
    }
    if (ch >= 'A' && ch <= 'F') {
        return 10 + (ch - 'A');
    }
    return -1;
}

static bool read_line_from_stdin(char out[DEVICE_INPUT_MAX]) {
    size_t offset = 0;
    while (offset + 1U < DEVICE_INPUT_MAX) {
        char ch;
        ssize_t count = read(STDIN_FILENO, &ch, 1U);
        if (count < 0) {
            if (errno == EINTR) {
                continue;
            }
            return false;
        }
        if (count == 0) {
            break;
        }
        if (ch == '\n') {
            break;
        }
        if (ch != '\r') {
            out[offset++] = ch;
        }
    }
    out[offset] = '\0';
    return offset > 0;
}

static bool parse_hex(const char *text, size_t text_len, uint8_t *out, size_t out_len) {
    size_t i;
    if (text == NULL || out == NULL || text_len != out_len * 2U) {
        return false;
    }
    for (i = 0; i < out_len; i++) {
        int hi = hex_value(text[i * 2U]);
        int lo = hex_value(text[i * 2U + 1U]);
        if (hi < 0 || lo < 0) {
            return false;
        }
        out[i] = (uint8_t)((hi << 4) | lo);
    }
    return true;
}

static void encode_hex(const uint8_t *input, size_t input_len, char *output) {
    static const char digits[] = "0123456789abcdef";
    size_t i;
    for (i = 0; i < input_len; i++) {
        output[i * 2U] = digits[input[i] >> 4];
        output[i * 2U + 1U] = digits[input[i] & 0x0fU];
    }
    output[input_len * 2U] = '\0';
}

static bool fill_random(uint8_t *output, size_t output_len) {
    size_t offset = 0;
#ifdef __NR_getrandom
    while (offset < output_len) {
        ssize_t count = syscall(
            __NR_getrandom,
            output + offset,
            output_len - offset,
            0
        );
        if (count < 0) {
            if (errno == EINTR) {
                continue;
            }
            break;
        }
        offset += (size_t)count;
    }
    if (offset == output_len) {
        return true;
    }
#endif
    {
        int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
        if (fd < 0) {
            return false;
        }
        offset = 0;
        while (offset < output_len) {
            ssize_t count = read(fd, output + offset, output_len - offset);
            if (count < 0) {
                if (errno == EINTR) {
                    continue;
                }
                close(fd);
                return false;
            }
            if (count == 0) {
                close(fd);
                return false;
            }
            offset += (size_t)count;
        }
        close(fd);
    }
    return true;
}

static bool payload_id_string(char out[NEBUMTK_WRAP_MAX_PAYLOAD_ID + 1U]) {
    size_t i;
    size_t length = (size_t)g_nebumtk_header.payload_id_len;
    if (length == 0 || length > NEBUMTK_WRAP_MAX_PAYLOAD_ID) {
        return false;
    }
    for (i = 0; i < length; i++) {
        uint8_t ch = g_nebumtk_payload_id[i];
        if (!((ch >= 'A' && ch <= 'Z') ||
              (ch >= 'a' && ch <= 'z') ||
              (ch >= '0' && ch <= '9') ||
              ch == '_' || ch == '.' || ch == '-')) {
            return false;
        }
        out[i] = (char)ch;
    }
    out[length] = '\0';
    return true;
}

static bool emit_device_challenge(
    const char *payload_id,
    const uint8_t challenge_nonce[32],
    const uint8_t device_public[32]
) {
    char nonce_hex[65];
    char public_hex[65];
    encode_hex(challenge_nonce, 32U, nonce_hex);
    encode_hex(device_public, 32U, public_hex);
    if (fprintf(
            stdout,
            "%s:%s:%s:%s\n",
            DEVICE_CHALLENGE_VERSION,
            payload_id,
            nonce_hex,
            public_hex
        ) < 0) {
        return false;
    }
    return fflush(stdout) == 0;
}

static bool decimal_string_is_valid(const char *value) {
    size_t i;
    size_t length = value == NULL ? 0U : strlen(value);
    if (length == 0 || length > 19U) {
        return false;
    }
    for (i = 0; i < length; i++) {
        if (value[i] < '0' || value[i] > '9') {
            return false;
        }
    }
    return true;
}

static bool lower_hex_string_is_valid(const char *value, size_t length) {
    size_t i;
    if (value == NULL || strlen(value) != length) {
        return false;
    }
    for (i = 0; i < length; i++) {
        if (!((value[i] >= '0' && value[i] <= '9') ||
              (value[i] >= 'a' && value[i] <= 'f'))) {
            return false;
        }
    }
    return true;
}

static bool parse_device_grant(
    char *line,
    const char *payload_id,
    const uint8_t challenge_nonce[32],
    const uint8_t device_private[32],
    const uint8_t device_public[32],
    uint8_t payload_key[32]
) {
    char *fields[6];
    char *cursor = line;
    char *separator;
    uint8_t server_public[32];
    uint8_t grant_nonce[12];
    uint8_t grant_ciphertext[DEVICE_GRANT_CIPHERTEXT_LEN];
    uint8_t shared_secret[32];
    uint8_t wrapping_key[32];
    uint8_t zero[32] = {0};
    char challenge_hex[65];
    char device_public_hex[65];
    char server_public_hex[65];
    char aad[DEVICE_AAD_MAX];
    int aad_len;
    size_t i;
    bool ok = false;

    for (i = 0; i < 6U; i++) {
        fields[i] = cursor;
        if (i == 5U) {
            if (strchr(cursor, ':') != NULL) {
                goto cleanup;
            }
            break;
        }
        separator = strchr(cursor, ':');
        if (separator == NULL) {
            goto cleanup;
        }
        *separator = '\0';
        cursor = separator + 1;
    }
    if (strcmp(fields[0], DEVICE_GRANT_VERSION) != 0 ||
        !lower_hex_string_is_valid(fields[1], 32U) ||
        !decimal_string_is_valid(fields[2]) ||
        !lower_hex_string_is_valid(fields[3], 64U) ||
        !lower_hex_string_is_valid(fields[4], 24U) ||
        !lower_hex_string_is_valid(fields[5], 96U) ||
        !parse_hex(fields[3], 64U, server_public, sizeof(server_public)) ||
        !parse_hex(fields[4], 24U, grant_nonce, sizeof(grant_nonce)) ||
        !parse_hex(
            fields[5],
            96U,
            grant_ciphertext,
            sizeof(grant_ciphertext)
        )) {
        goto cleanup;
    }
    crypto_x25519(shared_secret, device_private, server_public);
    if (crypto_verify32(shared_secret, zero) == 0) {
        goto cleanup;
    }
    encode_hex(challenge_nonce, 32U, challenge_hex);
    encode_hex(device_public, 32U, device_public_hex);
    encode_hex(server_public, 32U, server_public_hex);
    aad_len = snprintf(
        aad,
        sizeof(aad),
        "NEBULA-DEVICE-GRANT-v1|%s|%s|%s|%s|%s|%s",
        fields[1],
        payload_id,
        challenge_hex,
        device_public_hex,
        server_public_hex,
        fields[2]
    );
    if (aad_len <= 0 || (size_t)aad_len >= sizeof(aad)) {
        goto cleanup;
    }
    if (nebumtk_hkdf_sha256(
            challenge_nonce,
            shared_secret,
            (const uint8_t *)aad,
            (size_t)aad_len,
            wrapping_key
        ) != 0) {
        goto cleanup;
    }
    if (nebumtk_aes256_gcm_decrypt(
            wrapping_key,
            grant_nonce,
            (const uint8_t *)aad,
            (size_t)aad_len,
            grant_ciphertext,
            32U,
            grant_ciphertext + 32U,
            payload_key
        ) != 0) {
        goto cleanup;
    }
    ok = true;

cleanup:
    nebumtk_secure_bzero(server_public, sizeof(server_public));
    nebumtk_secure_bzero(grant_nonce, sizeof(grant_nonce));
    nebumtk_secure_bzero(grant_ciphertext, sizeof(grant_ciphertext));
    nebumtk_secure_bzero(shared_secret, sizeof(shared_secret));
    nebumtk_secure_bzero(wrapping_key, sizeof(wrapping_key));
    nebumtk_secure_bzero(aad, sizeof(aad));
    return ok;
}

static bool header_is_valid(void) {
    if (memcmp(g_nebumtk_header.magic, NEBUMTK_WRAP_MAGIC, NEBUMTK_WRAP_MAGIC_LEN) != 0) {
        return false;
    }
    if (g_nebumtk_header.version != NEBUMTK_WRAP_VERSION) {
        return false;
    }
    if (g_nebumtk_header.arch != NEBUMTK_WRAP_ARCH_AARCH64) {
        return false;
    }
    if (g_nebumtk_header.api_level != NEBUMTK_WRAP_API_LEVEL) {
        return false;
    }
    if (g_nebumtk_header.payload_id_len == 0 || g_nebumtk_header.payload_id_len > NEBUMTK_WRAP_MAX_PAYLOAD_ID) {
        return false;
    }
    if (g_nebumtk_header.plain_size == 0 ||
        g_nebumtk_ciphertext_len == 0 ||
        g_nebumtk_ciphertext_len != (size_t)g_nebumtk_header.plain_size) {
        return false;
    }
    return true;
}

static int write_payload_to_memfd(const uint8_t key[32], int *fd_out, void **map_out) {
    uint8_t aad[sizeof(NebumtkWrapHeader) + NEBUMTK_WRAP_MAX_PAYLOAD_ID];
    uint8_t actual_sha[32];
    int fd = -1;
    void *map = MAP_FAILED;
    size_t aad_len = sizeof(NebumtkWrapHeader) + (size_t)g_nebumtk_header.payload_id_len;

    memcpy(aad, &g_nebumtk_header, sizeof(NebumtkWrapHeader));
    memcpy(aad + sizeof(NebumtkWrapHeader), g_nebumtk_payload_id, g_nebumtk_header.payload_id_len);

    fd = memfd_create_portable("nebumtk-preload", MFD_ALLOW_SEALING | MFD_EXEC);
    if (fd < 0 && errno == EINVAL) {
        fd = memfd_create_portable("nebumtk-preload", MFD_ALLOW_SEALING);
    }
    if (fd < 0) {
        return -1;
    }
    if (fd < 3) {
        int safe_fd = fcntl(fd, F_DUPFD, 3);
        close(fd);
        if (safe_fd < 0) {
            return -1;
        }
        fd = safe_fd;
    }
    if (ftruncate(fd, (off_t)g_nebumtk_header.plain_size) != 0) {
        close(fd);
        return -1;
    }

    map = mmap(NULL, g_nebumtk_header.plain_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (map == MAP_FAILED) {
        close(fd);
        return -1;
    }

    if (nebumtk_aes256_gcm_decrypt(
            key,
            g_nebumtk_header.nonce,
            aad,
            aad_len,
            g_nebumtk_ciphertext,
            g_nebumtk_ciphertext_len,
            g_nebumtk_tag,
            (uint8_t *)map) != 0) {
        nebumtk_secure_bzero(map, g_nebumtk_header.plain_size);
        munmap(map, g_nebumtk_header.plain_size);
        close(fd);
        return -1;
    }

    if (nebumtk_sha256((const uint8_t *)map, g_nebumtk_header.plain_size, actual_sha) != 0 ||
        memcmp(actual_sha, g_nebumtk_header.plain_sha256, sizeof(actual_sha)) != 0) {
        nebumtk_secure_bzero(actual_sha, sizeof(actual_sha));
        nebumtk_secure_bzero(map, g_nebumtk_header.plain_size);
        munmap(map, g_nebumtk_header.plain_size);
        close(fd);
        return -1;
    }
    nebumtk_secure_bzero(actual_sha, sizeof(actual_sha));

    (void)fchmod(fd, S_IRUSR | S_IXUSR);
    (void)msync(map, g_nebumtk_header.plain_size, MS_SYNC);
    if (munmap(map, g_nebumtk_header.plain_size) != 0) {
        close(fd);
        return -1;
    }
    map = MAP_FAILED;
    if (fcntl(fd, F_ADD_SEALS, F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_WRITE | F_SEAL_SEAL) != 0) {
        close(fd);
        return -1;
    }

    *fd_out = fd;
    *map_out = map;
    return 0;
}

static int copy_payload_to_anonymous_inode(
    int source_fd,
    const char **failure_stage
) {
    uint8_t buffer[16384];
    char target_path[64];
    int target_fd = -1;
    int executable_fd = -1;

    *failure_stage = "exec-anon-open";
    target_fd = open(
        "/data/local/tmp",
        O_TMPFILE | O_RDWR | O_CLOEXEC,
        S_IRUSR | S_IWUSR | S_IXUSR
    );
    if (target_fd < 0) {
        return -1;
    }
    if (target_fd < 3) {
        int safe_fd = fcntl(target_fd, F_DUPFD_CLOEXEC, 3);
        int duplicate_error = errno;
        close(target_fd);
        if (safe_fd < 0) {
            errno = duplicate_error;
            return -1;
        }
        target_fd = safe_fd;
    }

    *failure_stage = "exec-anon-copy";
    if (lseek(source_fd, 0, SEEK_SET) < 0) {
        close(target_fd);
        return -1;
    }
    for (;;) {
        ssize_t count = read(source_fd, buffer, sizeof(buffer));
        size_t offset = 0;
        if (count < 0) {
            if (errno == EINTR) {
                continue;
            }
            nebumtk_secure_bzero(buffer, sizeof(buffer));
            close(target_fd);
            return -1;
        }
        if (count == 0) {
            break;
        }
        while (offset < (size_t)count) {
            ssize_t written = write(
                target_fd,
                buffer + offset,
                (size_t)count - offset
            );
            if (written < 0) {
                if (errno == EINTR) {
                    continue;
                }
                nebumtk_secure_bzero(buffer, sizeof(buffer));
                close(target_fd);
                return -1;
            }
            if (written == 0) {
                nebumtk_secure_bzero(buffer, sizeof(buffer));
                close(target_fd);
                errno = EIO;
                return -1;
            }
            offset += (size_t)written;
        }
    }
    nebumtk_secure_bzero(buffer, sizeof(buffer));

    *failure_stage = "exec-anon-mode";
    if (fchmod(target_fd, S_IRUSR | S_IXUSR) != 0) {
        close(target_fd);
        return -1;
    }

    /* An O_RDWR descriptor causes execveat() to fail with ETXTBSY. Reopen
     * the same unnamed inode read-only before closing its writable handle. */
    *failure_stage = "exec-anon-reopen";
    if (snprintf(
            target_path,
            sizeof(target_path),
            "/proc/self/fd/%d",
            target_fd
        ) < 0) {
        close(target_fd);
        return -1;
    }
    executable_fd = open(target_path, O_RDONLY | O_CLOEXEC);
    if (executable_fd < 0) {
        close(target_fd);
        return -1;
    }
    close(target_fd);
    return executable_fd;
}

int main(int argc, char **argv) {
    const char *key_arg = NULL;
    const char *execution_mode = NULL;
    char stdin_input[DEVICE_INPUT_MAX] = {0};
    char payload_id[NEBUMTK_WRAP_MAX_PAYLOAD_ID + 1U] = {0};
    int cmd_index = -1;
    int i;
    uint8_t key[32] = {0};
    uint8_t device_private[DEVICE_KEY_LEN] = {0};
    uint8_t device_public[DEVICE_KEY_LEN] = {0};
    uint8_t challenge_nonce[DEVICE_CHALLENGE_NONCE_LEN] = {0};
    uint8_t zero[DEVICE_KEY_LEN] = {0};
    int fd = -1;
    void *map = MAP_FAILED;
    char fd_path[64];
    char preload_arg[96];

    if (!header_is_valid()) {
        return fail_stage("header", 0);
    }

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-s") == 0) {
            if (i + 1 >= argc) {
                print_usage(argv[0]);
                return 2;
            }
            key_arg = argv[i + 1];
            i += 1;
            continue;
        }
        if (strcmp(argv[i], "-m") == 0) {
            if (i + 1 >= argc) {
                print_usage(argv[0]);
                return 2;
            }
            execution_mode = argv[i + 1];
            i += 1;
            continue;
        }
        if (strcmp(argv[i], "--") == 0) {
            cmd_index = i + 1;
            break;
        }
    }

    if (key_arg == NULL || strcmp(key_arg, "-") != 0 ||
        execution_mode == NULL ||
        (strcmp(execution_mode, "preload") != 0 && strcmp(execution_mode, "exec") != 0) ||
        cmd_index <= 0 || cmd_index >= argc) {
        print_usage(argv[0]);
        return 2;
    }
    if (prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0) {
        /* best effort */
    }
    if (!payload_id_string(payload_id) ||
        !fill_random(device_private, sizeof(device_private)) ||
        !fill_random(challenge_nonce, sizeof(challenge_nonce)) ||
        crypto_verify32(device_private, zero) == 0 ||
        crypto_verify32(challenge_nonce, zero) == 0) {
        return fail_stage("challenge", errno);
    }
    crypto_x25519_public_key(device_public, device_private);
    if (!emit_device_challenge(
            payload_id,
            challenge_nonce,
            device_public
        ) ||
        !read_line_from_stdin(stdin_input) ||
        !parse_device_grant(
            stdin_input,
            payload_id,
            challenge_nonce,
            device_private,
            device_public,
            key
        )) {
        nebumtk_secure_bzero(stdin_input, sizeof(stdin_input));
        nebumtk_secure_bzero(device_private, sizeof(device_private));
        nebumtk_secure_bzero(device_public, sizeof(device_public));
        nebumtk_secure_bzero(challenge_nonce, sizeof(challenge_nonce));
        return fail_stage("grant", errno);
    }
    nebumtk_secure_bzero(stdin_input, sizeof(stdin_input));
    nebumtk_secure_bzero(device_private, sizeof(device_private));
    nebumtk_secure_bzero(device_public, sizeof(device_public));
    nebumtk_secure_bzero(challenge_nonce, sizeof(challenge_nonce));

    if (write_payload_to_memfd(key, &fd, &map) != 0) {
        int materialize_error = errno;
        nebumtk_secure_bzero(key, sizeof(key));
        return fail_stage("materialize", materialize_error);
    }
    (void)close(STDIN_FILENO);
    nebumtk_secure_bzero(key, sizeof(key));

    if (snprintf(fd_path, sizeof(fd_path), "/proc/self/fd/%d", fd) < 0) {
        munmap(map, g_nebumtk_header.plain_size);
        close(fd);
        return fail_stage("fd-path", errno);
    }
    if (strcmp(execution_mode, "exec") == 0) {
        const char *anonymous_failure_stage = NULL;
        int anonymous_fd;
        int direct_exec_error;

        if (strcmp(argv[cmd_index], "__NEBUMTK_PAYLOAD__") != 0) {
            close(fd);
            return fail_stage("exec-placeholder", 0);
        }
        (void)unsetenv("LD_PRELOAD");
        argv[cmd_index] = fd_path;
        execv(fd_path, &argv[cmd_index]);
        direct_exec_error = errno;
        if (direct_exec_error != EACCES) {
            close(fd);
            return fail_stage("exec", direct_exec_error);
        }

        /*
         * Some Android SELinux policies reject execve() on a memfd even when
         * executable memfds are supported by the kernel.  Keep the fallback
         * unnamed: O_TMPFILE creates no directory entry and the inode is
         * destroyed automatically when its final descriptor is closed.
         */
        anonymous_fd = copy_payload_to_anonymous_inode(
            fd,
            &anonymous_failure_stage
        );
        if (anonymous_fd < 0) {
            int anonymous_error = errno;
            close(fd);
            return fail_stage(anonymous_failure_stage, anonymous_error);
        }
        close(fd);
        fd = -1;
        argv[cmd_index] = (char *)"protected-payload";
        execveat_portable(anonymous_fd, &argv[cmd_index]);
        {
            int anonymous_exec_error = errno;
            close(anonymous_fd);
            return fail_stage("exec-anon", anonymous_exec_error);
        }
    } else {
        bool placeholder_replaced = false;
        const char *anonymous_failure_stage = NULL;
        int anonymous_fd;
        int descriptor_flags;

        /* Select the anonymous filesystem-backed carrier before executing the
         * trigger. Unlike a post-exec retry, this cannot run the payload
         * twice. It avoids Android policies that reject executable mappings
         * from memfd; older filesystems may safely retain the sealed memfd. */
        anonymous_fd = copy_payload_to_anonymous_inode(
            fd,
            &anonymous_failure_stage
        );
        if (anonymous_fd >= 0) {
            descriptor_flags = fcntl(anonymous_fd, F_GETFD);
            if (descriptor_flags < 0 ||
                fcntl(
                    anonymous_fd,
                    F_SETFD,
                    descriptor_flags & ~FD_CLOEXEC
                ) != 0) {
                int inherit_error = errno;
                close(anonymous_fd);
                close(fd);
                return fail_stage("preload-anon-inherit", inherit_error);
            }
            close(fd);
            fd = anonymous_fd;
            if (snprintf(fd_path, sizeof(fd_path), "/proc/self/fd/%d", fd) < 0) {
                close(fd);
                return fail_stage("fd-path", errno);
            }
        }

        if (snprintf(
                preload_arg,
                sizeof(preload_arg),
                "LD_PRELOAD=%s",
                fd_path
            ) < 0) {
            close(fd);
            return fail_stage("preload-arg", errno);
        }
        for (i = cmd_index; i < argc; i++) {
            if (strcmp(argv[i], "LD_PRELOAD=__NEBUMTK_MEMFD__") == 0) {
                if (placeholder_replaced) {
                    close(fd);
                    return fail_stage("preload-placeholder", 0);
                }
                argv[i] = preload_arg;
                placeholder_replaced = true;
            }
        }
        if (placeholder_replaced) {
            /* The clean-env command supplies LD_PRELOAD only to its final
             * trigger. Do not preload the payload into toybox env itself. */
            if (unsetenv("LD_PRELOAD") != 0) {
                close(fd);
                return fail_stage("preload-env", errno);
            }
        } else if (setenv("LD_PRELOAD", fd_path, 1) != 0) {
            close(fd);
            return fail_stage("preload-env", errno);
        }
        execvp(argv[cmd_index], &argv[cmd_index]);
        return fail_stage("trigger", errno);
    }

    if (map != MAP_FAILED) {
        munmap(map, g_nebumtk_header.plain_size);
    }
    close(fd);
    return fail_stage("exit", errno);
}
