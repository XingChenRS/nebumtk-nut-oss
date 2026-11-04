#ifndef NEBUMTK_WRAPPER_PROTOCOL_H
#define NEBUMTK_WRAPPER_PROTOCOL_H

#include <stdint.h>

#define NEBUMTK_WRAP_MAGIC "NBWRAP1"
#define NEBUMTK_WRAP_MAGIC_LEN 7
#define NEBUMTK_WRAP_VERSION 1u
#define NEBUMTK_WRAP_ARCH_AARCH64 1u
#define NEBUMTK_WRAP_API_LEVEL 35u
#define NEBUMTK_WRAP_NONCE_LEN 12u
#define NEBUMTK_WRAP_TAG_LEN 16u
#define NEBUMTK_WRAP_SHA256_LEN 32u
#define NEBUMTK_WRAP_MAX_PAYLOAD_ID 96u

#pragma pack(push, 1)
typedef struct NebumtkWrapHeader {
    char magic[NEBUMTK_WRAP_MAGIC_LEN];
    uint8_t version;
    uint8_t arch;
    uint16_t api_level;
    uint16_t payload_id_len;
    uint16_t reserved;
    uint32_t plain_size;
    uint8_t plain_sha256[NEBUMTK_WRAP_SHA256_LEN];
    uint8_t nonce[NEBUMTK_WRAP_NONCE_LEN];
} NebumtkWrapHeader;
#pragma pack(pop)

#endif
