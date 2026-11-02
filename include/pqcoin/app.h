#ifndef PQCOIN_APP_H
#define PQCOIN_APP_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
const char *pq_error(void);
void *pq_open(const char *directory, const unsigned char *genesis, size_t size);
void pq_close(void *app);
int pq_info(void *app, uint64_t *height, unsigned char *hash);
int pq_check(void *app, const unsigned char *tx, size_t size);
int pq_reset(void *app);
int pq_apply(void *app, const unsigned char *tx, size_t size);
int pq_stage(void *app, uint64_t height, unsigned char *hash);
int pq_commit(void *app);
int pq_account(void *app, const unsigned char *owner, uint64_t *balance,
               uint64_t *nonce);
#ifdef __cplusplus
}
#endif
#endif
