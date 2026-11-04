#ifndef NEBUMTK_WRAPPER_CRYPTO_H
#define NEBUMTK_WRAPPER_CRYPTO_H

#include <stddef.h>
#include <stdint.h>

typedef struct NebumtkAes256Key {
    void *enc_state;
} NebumtkAes256Key;

int nebumtk_aes256_init(NebumtkAes256Key *key, const uint8_t raw[32]);
void nebumtk_aes256_free(NebumtkAes256Key *key);
void nebumtk_aes256_block(const NebumtkAes256Key *key, const uint8_t in[16], uint8_t out[16]);

int nebumtk_ghash_expand(const uint8_t hash_subkey[16], void **state);
void nebumtk_ghash_destroy(void *state);
int nebumtk_ghash_update(
    void *state,
    uint8_t y_out[16],
    const uint8_t *block_data,
    size_t len,
    const uint8_t y_in[16]
);

void nebumtk_secure_bzero(void *ptr, size_t len);
int nebumtk_sha256(const uint8_t *data, size_t len, uint8_t out[32]);
int nebumtk_hkdf_sha256(
    const uint8_t salt[32],
    const uint8_t input_key[32],
    const uint8_t *info,
    size_t info_len,
    uint8_t out[32]
);
int nebumtk_aes256_gcm_decrypt(
    const uint8_t raw_key[32],
    const uint8_t nonce[12],
    const uint8_t *aad,
    size_t aad_len,
    const uint8_t *ciphertext,
    size_t ciphertext_len,
    const uint8_t tag[16],
    uint8_t *plaintext_out
);

#endif
