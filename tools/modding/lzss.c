/* Fast versions of lzss_decompress / lzss_compress_optimal in clone_character.py, loaded with ctypes.
 * Output must stay byte-identical to the Python code; tools/modding/test_lzss.py checks it.
 * Build: gcc -O2 -shared -fPIC -o _lzss.so lzss.c   (Windows: -o _lzss.dll -static-libgcc) */
#include <stdint.h>
#include <stdlib.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

typedef struct {
    uint8_t *out;
    size_t len;
    uint32_t cur;
    int n;
} BitWriter;

static void put_word(BitWriter *bw, uint32_t w)
{
    bw->out[bw->len++] = (uint8_t)(w >> 24);
    bw->out[bw->len++] = (uint8_t)(w >> 16);
    bw->out[bw->len++] = (uint8_t)(w >> 8);
    bw->out[bw->len++] = (uint8_t)w;
}

/* Same bit order as _BitWriter.write: a value that straddles words puts its high bits in the old word. */
static void put_bits(BitWriter *bw, uint32_t value, int cnt)
{
    int space = 32 - bw->n;
    value &= (1u << cnt) - 1;
    if (cnt <= space) {
        bw->cur |= value << bw->n;
        bw->n += cnt;
        if (bw->n == 32) {
            put_word(bw, bw->cur);
            bw->cur = 0;
            bw->n = 0;
        }
    } else {
        int low = cnt - space;
        bw->cur |= (value >> low) << bw->n;
        put_word(bw, bw->cur);
        bw->cur = value & ((1u << low) - 1);
        bw->n = low;
    }
}

/* Optimal parse: longest match per position (most recent candidate wins ties, at most max_chain
 * tries within the window), then the cheapest literal/match path, shortest length on equal cost.
 * `out` needs n * 9 / 8 + 64 bytes.  Returns the byte count before the 32-byte padding, or 0 when
 * out of memory. */
EXPORT size_t lzss_compress_optimal(const uint8_t *data, size_t n, uint8_t *out, int lb_bits, int rp_bits,
                                    int max_chain)
{
    size_t max_back = (size_t)1 << lb_bits;
    size_t max_len = ((size_t)1 << rp_bits) - 1 + 2;
    int32_t *head = malloc(65536 * sizeof(int32_t));
    int32_t *prev = malloc((n + 1) * sizeof(int32_t));
    uint16_t *best_len = calloc(n + 1, sizeof(uint16_t));
    uint16_t *best_back = calloc(n + 1, sizeof(uint16_t));
    uint32_t *cost = malloc((n + 1) * sizeof(uint32_t));
    uint16_t *step = malloc((n + 1) * sizeof(uint16_t));
    uint32_t lit_bits = 9, match_bits = 1 + lb_bits + rp_bits;
    BitWriter bw = { out, 0, 0, 0 };
    size_t pos, i;

    if (!head || !prev || !best_len || !best_back || !cost || !step) {
        bw.len = 0;
        goto done;
    }
    for (i = 0; i < 65536; i++)
        head[i] = -1;

    for (pos = 0; pos + 1 < n; pos++) {
        unsigned key = ((unsigned)data[pos] << 8) | data[pos + 1];
        int32_t cand = head[key];
        size_t limit = max_len < n - pos ? max_len : n - pos;
        size_t blen = 0, bback = 0;
        int tries = 0;
        while (cand >= 0) {
            size_t l = 2;
            if (pos - (size_t)cand > max_back || tries >= max_chain)
                break;
            tries++;
            while (l < limit && data[cand + l] == data[pos + l])
                l++;
            if (l > blen) {
                blen = l;
                bback = pos - (size_t)cand;
                if (l == limit)
                    break;
            }
            cand = prev[cand];
        }
        best_len[pos] = (uint16_t)blen;
        best_back[pos] = (uint16_t)bback;
        prev[pos] = head[key];
        head[key] = (int32_t)pos;
    }

    cost[n] = 0;
    for (pos = n; pos-- > 0;) {
        uint32_t best = lit_bits + cost[pos + 1];
        size_t l;
        step[pos] = 1;
        for (l = 2; l <= best_len[pos]; l++) {
            uint32_t v = match_bits + cost[pos + l];
            if (v < best) {
                best = v;
                step[pos] = (uint16_t)l;
            }
        }
        cost[pos] = best;
    }

    for (pos = 0; pos < n; pos += step[pos]) {
        if (step[pos] == 1) {
            put_bits(&bw, 1, 1);
            put_bits(&bw, data[pos], 8);
        } else {
            put_bits(&bw, 0, 1);
            put_bits(&bw, best_back[pos] - 1, lb_bits);
            put_bits(&bw, step[pos] - 2, rp_bits);
        }
    }
    if (bw.n)
        put_word(&bw, bw.cur);

done:
    free(head);
    free(prev);
    free(best_len);
    free(best_back);
    free(cost);
    free(step);
    return bw.len;
}

/* lzss_decompress: expand exactly `size` bytes.  Returns the input bytes consumed, -1 when the stream
 * runs out, -2 on a bad back-reference. */
EXPORT long long lzss_decompress(const uint8_t *data, size_t n, uint8_t *out, size_t size, int lb_bits,
                                 int rp_bits)
{
    size_t pos = 0, len = 0;
    uint32_t buf = 0;
    int nbits = 0;

#define READ_BITS(dst, cnt)                                                                  \
    do {                                                                                     \
        int c_ = (cnt);                                                                      \
        if (nbits >= c_) {                                                                   \
            (dst) = buf & ((1u << c_) - 1);                                                  \
            buf >>= c_;                                                                      \
            nbits -= c_;                                                                     \
        } else {                                                                             \
            int need_ = c_ - nbits, rem_ = 32 - need_;                                       \
            uint32_t w_, v_ = (uint32_t)((uint64_t)buf << need_);                            \
            if (pos + 4 > n)                                                                 \
                return -1;                                                                   \
            w_ = ((uint32_t)data[pos] << 24) | ((uint32_t)data[pos + 1] << 16) |             \
                 ((uint32_t)data[pos + 2] << 8) | data[pos + 3];                             \
            pos += 4;                                                                        \
            v_ |= (uint32_t)((uint64_t)w_ << rem_) >> rem_;                                  \
            nbits = rem_;                                                                    \
            buf = (uint32_t)((uint64_t)w_ >> need_);                                         \
            (dst) = v_;                                                                      \
        }                                                                                    \
    } while (0)

    while (len < size) {
        uint32_t flag, v;
        READ_BITS(flag, 1);
        if (flag) {
            READ_BITS(v, 8);
            out[len++] = (uint8_t)v;
        } else {
            uint32_t back, cnt;
            READ_BITS(back, lb_bits);
            READ_BITS(cnt, rp_bits);
            back += 1;
            cnt += 2;
            if (back > len || cnt > size - len)
                return -2;
            while (cnt--) {
                out[len] = out[len - back];
                len++;
            }
        }
    }
#undef READ_BITS
    return (long long)pos;
}
