/*
 * vec_affine_sched 单线程 Uprobe 级用户态 loader
 * 编译：gcc -O2 -Wall -o loader loader.c -lbpf -lelf -lz
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <time.h>
#include <errno.h>
#include <sys/stat.h>
#include <getopt.h>
#include <bpf/libbpf.h>
#include <bpf/bpf.h>
#include "vec_affine_sched.skel.h"

static const char *stat_names[] = {
	"enqueue_total", "select_worker_ai_core", "select_other_core", "inference_threads", "protect_window",
};
#define NR_STATS (sizeof(stat_names) / sizeof(stat_names[0]))

static volatile sig_atomic_t exit_flag = 0;
static const char *opt_pin_dir = "/sys/fs/bpf/vecsched";
static const char *opt_target_bin = NULL;
static int opt_stats_interval = 5;

static struct bpf_link *uprobe_link = NULL, *uretprobe_link = NULL;

static void sig_handler(int sig) { exit_flag = 1; }

static void get_time_str(char *buf, size_t size)
{
	time_t t = time(NULL);
	struct tm tm;
	localtime_r(&t, &tm);
	strftime(buf, size, "%H:%M:%S", &tm);
}

static int read_stats(struct vec_affine_sched_bpf *skel, uint64_t *totals)
{
	int fd = bpf_map__fd(skel->maps.stats);
	int nr_cpus = libbpf_num_possible_cpus();
	uint64_t *percpu_vals = malloc(nr_cpus * sizeof(uint64_t));
	if (!percpu_vals)
		return -1;

	memset(totals, 0, NR_STATS * sizeof(uint64_t));
	for (uint32_t key = 0; key < NR_STATS; key++) {
		if (bpf_map_lookup_elem(fd, &key, percpu_vals) != 0)
			continue;
		for (int cpu = 0; cpu < nr_cpus; cpu++)
			totals[key] += percpu_vals[cpu];
	}
	free(percpu_vals);
	return 0;
}

static void print_stats(uint64_t *totals)
{
	char ts[16];
	get_time_str(ts, sizeof(ts));
	printf("[%s]", ts);
	for (int i = 0; i < NR_STATS; i++)
		printf(" %s=%lu", stat_names[i], totals[i]);
	printf("\n");
}

static int mkdir_p(const char *dir)
{
	char tmp[256];
	snprintf(tmp, sizeof(tmp), "%s", dir);
	for (char *p = tmp + 1; *p; p++) {
		if (*p == '/') {
			*p = 0;
			if (mkdir(tmp, 0755) && errno != EEXIST) return -1;
			*p = '/';
		}
	}
	if (mkdir(tmp, 0755) && errno != EEXIST) return -1;
	return 0;
}

static void attach_uprobes(struct vec_affine_sched_bpf *skel)
{
	if (!opt_target_bin) {
		fprintf(stderr, "提示: 未提供 --target-bin 参数，可通过 -t 指定 libggml-cpu.so 路径以启用 Uprobe 精准标记\n");
		return;
	}

	LIBBPF_OPTS(bpf_uprobe_opts, opts,
		.func_name = "ggml_compute_forward_mul_mat",
		.retprobe = false,
	);
	uprobe_link = bpf_program__attach_uprobe_opts(
		skel->progs.vecsched_mul_mat_entry, -1,
		opt_target_bin, 0, &opts);
	if (!uprobe_link) {
		fprintf(stderr, "警告: uprobe attach 到 %s 失败\n", opt_target_bin);
	}

	opts.retprobe = true;
	uretprobe_link = bpf_program__attach_uprobe_opts(
		skel->progs.vecsched_mul_mat_exit, -1,
		opt_target_bin, 0, &opts);
	if (!uretprobe_link) {
		fprintf(stderr, "警告: uretprobe attach 失败\n");
	}

	if (uprobe_link && uretprobe_link) {
		fprintf(stderr, "成功: Uprobe 已精准挂载至 %s 的 ggml_compute_forward_mul_mat!\n", opt_target_bin);
	}
}

int main(int argc, char **argv)
{
	struct vec_affine_sched_bpf *skel = NULL;
	int err;

	static struct option long_options[] = {
		{"pin-dir", required_argument, 0, 'd'},
		{"target-bin", required_argument, 0, 't'},
		{"stats-interval", required_argument, 0, 'i'},
		{0, 0, 0, 0}
	};
	while (1) {
		int c = getopt_long(argc, argv, "d:t:i:", long_options, NULL);
		if (c == -1) break;
		switch (c) {
		case 'd': opt_pin_dir = optarg; break;
		case 't': opt_target_bin = optarg; break;
		case 'i': opt_stats_interval = atoi(optarg); break;
		default:
			fprintf(stderr, "用法: %s [-t /path/to/libggml-cpu.so] [--pin-dir DIR] [--stats-interval SEC]\n", argv[0]);
			return 1;
		}
	}

	skel = vec_affine_sched_bpf__open();
	if (!skel) {
		fprintf(stderr, "打开 BPF 对象失败\n");
		return 1;
	}

	bpf_program__set_autoattach(skel->progs.vecsched_mul_mat_entry, false);
	bpf_program__set_autoattach(skel->progs.vecsched_mul_mat_exit, false);

	err = vec_affine_sched_bpf__load(skel);
	if (err) {
		fprintf(stderr, "加载 BPF 对象失败: %d\n", err);
		vec_affine_sched_bpf__destroy(skel);
		return 1;
	}

	err = vec_affine_sched_bpf__attach(skel);
	if (err) {
		fprintf(stderr, "attach 调度器失败: %d\n", err);
		vec_affine_sched_bpf__destroy(skel);
		return 1;
	}
	fprintf(stderr, "调度器已成功加载并开启!\n");

	attach_uprobes(skel);

	if (mkdir_p(opt_pin_dir) == 0) {
		char path[256];
		snprintf(path, sizeof(path), "%s/stats", opt_pin_dir);
		unlink(path);
		bpf_map__pin(skel->maps.stats, path);
	}

	signal(SIGINT, sig_handler);
	signal(SIGTERM, sig_handler);

	while (!exit_flag) {
		sleep(opt_stats_interval);
		uint64_t totals[NR_STATS];
		if (read_stats(skel, totals) == 0) {
			print_stats(totals);
			fflush(stdout);
		}
	}

	fprintf(stderr, "正在卸载调度器，回退至系统原生 CFS 调度器...\n");
	if (uprobe_link) bpf_link__destroy(uprobe_link);
	if (uretprobe_link) bpf_link__destroy(uretprobe_link);

	vec_affine_sched_bpf__detach(skel);

	char path[256];
	snprintf(path, sizeof(path), "%s/stats", opt_pin_dir);
	bpf_map__unpin(skel->maps.stats, path);

	vec_affine_sched_bpf__destroy(skel);
	return 0;
}