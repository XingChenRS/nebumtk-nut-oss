#include "wrapper_crypto.h"

#include "vendor/pycryptodome/src/block_base.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

int AES_start_operation(const uint8_t key[], size_t key_len, void **state);
int AES_stop_operation(BlockBase *state);

int ghash_portable(
    uint8_t y_out[16],
    const uint8_t block_data[],
    size_t len,
    const uint8_t y_in[16],
    const void *exp_key
);
int ghash_expand_portable(const uint8_t h[16], void **ghash_tables);
int ghash_destroy_portable(void *ghash_tables);

int SHA256_init(void **state);
int SHA256_destroy(void *state);
int SHA256_update(void *state, const uint8_t *buf, size_t len);
int SHA256_digest(const void *state, uint8_t *digest, size_t digest_size);

static void store_u64_be(uint8_t out[8], uint64_t value) {
    out[0] = (uint8_t)(value >> 56);
    out[1] = (uint8_t)(value >> 48);
    out[2] = (uint8_t)(value >> 40);
    out[3] = (uint8_t)(value >> 32);
    out[4] = (uint8_t)(value >> 24);
    out[5] = (uint8_t)(value >> 16);
    out[6] = (uint8_t)(value >> 8);
    out[7] = (uint8_t)value;
}

static void increment_counter(uint8_t counter[16]) {
    uint32_t value = ((uint32_t)counter[12] << 24) |
                     ((uint32_t)counter[13] << 16) |
                     ((uint32_t)counter[14] << 8) |
                     (uint32_t)counter[15];
    value += 1U;
    counter[12] = (uint8_t)(value >> 24);
    counter[13] = (uint8_t)(value >> 16);
    counter[14] = (uint8_t)(value >> 8);
    counter[15] = (uint8_t)value;
}

static int constant_time_equal(const uint8_t *a, const uint8_t *b, size_t len) {
    size_t i;
    uint8_t diff = 0;
    for (i = 0; i < len; i++) {
        diff |= (uint8_t)(a[i] ^ b[i]);
    }
    return diff == 0;
}

void nebumtk_secure_bzero(void *ptr, size_t len) {
    volatile uint8_t *cursor = (volatile uint8_t *)ptr;
    while (len > 0) {
        *cursor++ = 0;
        len--;
    }
}

int nebumtk_aes256_init(NebumtkAes256Key *key, const uint8_t raw[32]) {
    if (key == NULL || raw == NULL) {
        return -1;
    }
    key->enc_state = NULL;
    return AES_start_operation(raw, 32U, &key->enc_state);
}

void nebumtk_aes256_free(NebumtkAes256Key *key) {
    if (key != NULL && key->enc_state != NULL) {
        AES_stop_operation((BlockBase *)key->enc_state);
        key->enc_state = NULL;
    }
}

void nebumtk_aes256_block(const NebumtkAes256Key *key, const uint8_t in[16], uint8_t out[16]) {
    const BlockBase *state = (const BlockBase *)key->enc_state;
    (void)state->encrypt(state, in, out, 16U);
}

int nebumtk_ghash_expand(const uint8_t hash_subkey[16], void **state) {
    return ghash_expand_portable(hash_subkey, state);
}

void nebumtk_ghash_destroy(void *state) {
    if (state != NULL) {
        (void)ghash_destroy_portable(state);
    }
}

int nebumtk_ghash_update(
    void *state,
    uint8_t y_out[16],
    const uint8_t *block_data,
    size_t len,
    const uint8_t y_in[16]
) {
    return ghash_portable(y_out, block_data, len, y_in, state);
}

static int ghash_section(void *ghash_state, uint8_t y[16], const uint8_t *data, size_t len) {
    size_t full = len & ~(size_t)15U;
    uint8_t tail[16];
    int result;

    if (full > 0) {
        result = nebumtk_ghash_update(ghash_state, y, data, full, y);
        if (result != 0) {
            return result;
        }
    }
    if (full != len) {
        memset(tail, 0, sizeof(tail));
        memcpy(tail, data + full, len - full);
        result = nebumtk_ghash_update(ghash_state, y, tail, sizeof(tail), y);
        nebumtk_secure_bzero(tail, sizeof(tail));
        return result;
    }
    return 0;
}

int nebumtk_sha256(const uint8_t *data, size_t len, uint8_t out[32]) {
    void *state = NULL;
    int result;

    if (data == NULL || out == NULL) {
        return -1;
    }
    result = SHA256_init(&state);
    if (result == 0) {
        result = SHA256_update(state, data, len);
    }
    if (result == 0) {
        result = SHA256_digest(state, out, 32U);
    }
    if (state != NULL) {
        (void)SHA256_destroy(state);
    }
    return result;
}

static int hmac_sha256(
    const uint8_t *key,
    size_t key_len,
    const uint8_t *first,
    size_t first_len,
    const uint8_t *second,
    size_t second_len,
    uint8_t out[32]
) {
    uint8_t ipad[64];
    uint8_t opad[64];
    uint8_t inner[32];
    void *state = NULL;
    size_t i;
    int result = -1;

    if (key == NULL || key_len > sizeof(ipad) || out == NULL) {
        return -1;
    }
    if ((first_len > 0 && first == NULL) || (second_len > 0 && second == NULL)) {
        return -1;
    }
    memset(ipad, 0x36, sizeof(ipad));
    memset(opad, 0x5c, sizeof(opad));
    for (i = 0; i < key_len; i++) {
        ipad[i] ^= key[i];
        opad[i] ^= key[i];
    }
    if (SHA256_init(&state) != 0 ||
        SHA256_update(state, ipad, sizeof(ipad)) != 0 ||
        (first_len > 0 && SHA256_update(state, first, first_len) != 0) ||
        (second_len > 0 && SHA256_update(state, second, second_len) != 0) ||
        SHA256_digest(state, inner, sizeof(inner)) != 0) {
        goto cleanup;
    }
    (void)SHA256_destroy(state);
    state = NULL;
    if (SHA256_init(&state) != 0 ||
        SHA256_update(state, opad, sizeof(opad)) != 0 ||
        SHA256_update(state, inner, sizeof(inner)) != 0 ||
        SHA256_digest(state, out, 32U) != 0) {
        goto cleanup;
    }
    result = 0;

cleanup:
    if (state != NULL) {
        (void)SHA256_destroy(state);
    }
    nebumtk_secure_bzero(ipad, sizeof(ipad));
    nebumtk_secure_bzero(opad, sizeof(opad));
    nebumtk_secure_bzero(inner, sizeof(inner));
    return result;
}

int nebumtk_hkdf_sha256(
    const uint8_t salt[32],
    const uint8_t input_key[32],
    const uint8_t *info,
    size_t info_len,
    uint8_t out[32]
) {
    uint8_t pseudorandom_key[32];
    const uint8_t counter = 1U;
    int result;

    if (salt == NULL || input_key == NULL || out == NULL) {
        return -1;
    }
    result = hmac_sha256(
        salt,
        32U,
        input_key,
        32U,
        NULL,
        0,
        pseudorandom_key
    );
    if (result == 0) {
        result = hmac_sha256(
            pseudorandom_key,
            sizeof(pseudorandom_key),
            info,
            info_len,
            &counter,
            1U,
            out
        );
    }
    nebumtk_secure_bzero(pseudorandom_key, sizeof(pseudorandom_key));
    return result;
}

int nebumtk_aes256_gcm_decrypt(
    const uint8_t raw_key[32],
    const uint8_t nonce[12],
    const uint8_t *aad,
    size_t aad_len,
    const uint8_t *ciphertext,
    size_t ciphertext_len,
    const uint8_t tag[16],
    uint8_t *plaintext_out
) {
    NebumtkAes256Key aes;
    void *ghash_state = NULL;
    uint8_t zero[16] = {0};
    uint8_t hash_subkey[16];
    uint8_t j0[16];
    uint8_t counter[16];
    uint8_t stream[16];
    uint8_t y[16] = {0};
    uint8_t lengths[16];
    uint8_t tag_mask[16];
    uint8_t computed_tag[16];
    size_t offset;
    int result = -1;

    if (raw_key == NULL || nonce == NULL || ciphertext == NULL || tag == NULL || plaintext_out == NULL) {
        return -1;
    }
    if (aad_len > 0 && aad == NULL) {
        return -1;
    }
    if (aad_len > UINT64_MAX / 8U || ciphertext_len > UINT64_MAX / 8U) {
        return -1;
    }

    memset(&aes, 0, sizeof(aes));
    if (nebumtk_aes256_init(&aes, raw_key) != 0) {
        goto cleanup;
    }

    nebumtk_aes256_block(&aes, zero, hash_subkey);
    if (nebumtk_ghash_expand(hash_subkey, &ghash_state) != 0) {
        goto cleanup;
    }

    memcpy(j0, nonce, 12U);
    j0[12] = 0;
    j0[13] = 0;
    j0[14] = 0;
    j0[15] = 1;
    memcpy(counter, j0, sizeof(counter));

    for (offset = 0; offset < ciphertext_len; offset += 16U) {
        size_t i;
        size_t chunk = ciphertext_len - offset;
        if (chunk > 16U) {
            chunk = 16U;
        }
        increment_counter(counter);
        nebumtk_aes256_block(&aes, counter, stream);
        for (i = 0; i < chunk; i++) {
            plaintext_out[offset + i] = ciphertext[offset + i] ^ stream[i];
        }
    }

    if (ghash_section(ghash_state, y, aad, aad_len) != 0) {
        goto cleanup;
    }
    if (ghash_section(ghash_state, y, ciphertext, ciphertext_len) != 0) {
        goto cleanup;
    }
    store_u64_be(lengths, (uint64_t)aad_len * 8U);
    store_u64_be(lengths + 8, (uint64_t)ciphertext_len * 8U);
    if (nebumtk_ghash_update(ghash_state, y, lengths, sizeof(lengths), y) != 0) {
        goto cleanup;
    }

    nebumtk_aes256_block(&aes, j0, tag_mask);
    for (offset = 0; offset < 16U; offset++) {
        computed_tag[offset] = (uint8_t)(tag_mask[offset] ^ y[offset]);
    }
    if (!constant_time_equal(computed_tag, tag, 16U)) {
        nebumtk_secure_bzero(plaintext_out, ciphertext_len);
        goto cleanup;
    }

    result = 0;

cleanup:
    nebumtk_ghash_destroy(ghash_state);
    nebumtk_aes256_free(&aes);
    nebumtk_secure_bzero(hash_subkey, sizeof(hash_subkey));
    nebumtk_secure_bzero(j0, sizeof(j0));
    nebumtk_secure_bzero(counter, sizeof(counter));
    nebumtk_secure_bzero(stream, sizeof(stream));
    nebumtk_secure_bzero(y, sizeof(y));
    nebumtk_secure_bzero(lengths, sizeof(lengths));
    nebumtk_secure_bzero(tag_mask, sizeof(tag_mask));
    nebumtk_secure_bzero(computed_tag, sizeof(computed_tag));
    return result;
}
