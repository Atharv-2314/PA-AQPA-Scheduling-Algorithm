#!/usr/bin/env python3
"""
analyze.py — PA-AQPA Experiment Analysis
Reads experiment CSVs, generates publication-quality plots, runs statistical tests.
"""

import os, sys, csv, math
from collections import defaultdict
from typing import List, Dict, Tuple

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy import stats as scipy_stats

plt.rcParams.update({
    'font.size': 11, 'font.family': 'sans-serif',
    'axes.titlesize': 14, 'axes.labelsize': 12,
    'figure.dpi': 150, 'savefig.bbox': 'tight',
})

COLORS = {'cpu':'#3498db','io':'#2ecc71','mixed':'#e74c3c','starvation':'#f39c12'}
COLOR_LIST = ['#3498db','#e74c3c','#2ecc71','#f39c12','#9b59b6','#1abc9c','#e67e22','#34495e']

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'experiment_data')
PLOTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'plots')
os.makedirs(PLOTS_DIR, exist_ok=True)


def mean_ci(data, confidence=0.95):
    n = len(data)
    if n == 0: return 0,0,0
    m = sum(data)/n
    if n == 1: return m,m,m
    se = scipy_stats.sem(data)
    ci = se * scipy_stats.t.ppf((1+confidence)/2, n-1)
    return m, m-ci, m+ci


def load_flat_csv(filepath):
    """Loads a single flat CSV file into a list (representing one trial/dataset)."""
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r') as csvfile:
        return list(csv.DictReader(csvfile))


def extract_trial_metrics(trials):
    results = defaultdict(list)
    for trial_data in trials:
        # Fallback to accepting all rows if 'state' column is missing in the new flat files
        completed = [r for r in trial_data if r.get("state", "DONE") == "DONE"]
        if not completed: continue
        
        waits = [float(r.get("wait_time", 0)) for r in completed if "wait_time" in r]
        turns = [float(r.get("turnaround", 0)) for r in completed if "turnaround" in r]
        resps = [float(r.get("response_time", 0)) for r in completed if float(r.get("response_time", 0)) >= 0]
        cs = sum(int(r.get("context_switches", 0)) for r in completed if "context_switches" in r)
        cpu_t = [float(r.get("cpu_time", 0)) for r in completed if "cpu_time" in r]

        if waits: results["avg_wait"].append(sum(waits)/len(waits))
        if turns: results["avg_turnaround"].append(sum(turns)/len(turns))
        if resps: results["avg_response"].append(sum(resps)/len(resps))
        
        results["total_cs"].append(cs)
        results["all_waits"].extend(waits)
        results["all_turnarounds"].extend(turns)

        if len(cpu_t) > 1:
            s=sum(cpu_t); s2=sum(x*x for x in cpu_t); n=len(cpu_t)
            results["fairness"].append((s*s)/(n*s2) if s2>0 else 1.0)
        else:
            results["fairness"].append(1.0)
    return dict(results)


# ======================== PLOTS ========================

def plot_wait_cdf():
    print("  [1] Waiting Time CDF...")
    fig, ax = plt.subplots(figsize=(8,5))
    has_data = False
    
    for wl in ["cpu","io","mixed","starvation"]:
        data = load_flat_csv(os.path.join(DATA_DIR, f"perproc_{wl}.csv"))
        if not data: continue
            
        m = extract_trial_metrics([data])
        waits = sorted(m.get("all_waits", []))
        if not waits: continue
            
        has_data = True
        ax.plot(waits, [(i+1)/len(waits) for i in range(len(waits))],
                label=wl.capitalize(), color=COLORS.get(wl,'#333'), linewidth=2)
                
    if not has_data:
        print("      -> Skipping: No wait time data found.")
        plt.close(fig); return

    ax.set_xlabel("Waiting Time (ticks)"); ax.set_ylabel("CDF")
    ax.set_title("Waiting Time CDF — PA-AQPA Across Workloads")
    ax.legend(framealpha=0.9); ax.grid(True, alpha=0.3)
    fig.savefig(os.path.join(PLOTS_DIR, "fig1_wait_cdf.png")); plt.close(fig)


def plot_turnaround_box():
    print("  [2] Turnaround Time Boxplots...")
    fig, ax = plt.subplots(figsize=(8,5))
    data_list, labels, colors = [],[],[]
    
    for wl in ["cpu","io","mixed","starvation"]:
        data = load_flat_csv(os.path.join(DATA_DIR, f"perproc_{wl}.csv"))
        if not data: continue
            
        m = extract_trial_metrics([data])
        if m.get("all_turnarounds"):
            data_list.append(m["all_turnarounds"])
            labels.append(wl.capitalize())
            colors.append(COLORS.get(wl,'#333'))
            
    if not data_list:
        print("      -> Skipping: No turnaround data found.")
        plt.close(fig); return

    bp = ax.boxplot(data_list, labels=labels, patch_artist=True, widths=0.6)
    for patch, c in zip(bp['boxes'], colors): patch.set_facecolor(c); patch.set_alpha(0.7)
    for med in bp['medians']: med.set_color('black'); med.set_linewidth(2)
    ax.set_ylabel("Turnaround Time (ticks)")
    ax.set_title("Turnaround Time Distribution")
    ax.grid(True, alpha=0.3, axis='y')
    fig.savefig(os.path.join(PLOTS_DIR, "fig2_turnaround_box.png")); plt.close(fig)


def plot_context_switches():
    print("  [3] Context Switches...")
    fig, ax = plt.subplots(figsize=(8,5))
    labels, means, err_lo, err_hi, cols = [],[],[],[],[]
    
    for wl in ["cpu","io","mixed","starvation"]:
        data = load_flat_csv(os.path.join(DATA_DIR, f"main_{wl}_paaqpa.csv"))
        if not data: continue
            
        m = extract_trial_metrics([data])
        cs = m.get("total_cs",[])
        if not cs: continue
            
        mean,lo,hi = mean_ci(cs)
        labels.append(wl.capitalize()); means.append(mean)
        err_lo.append(mean-lo); err_hi.append(hi-mean)
        cols.append(COLORS.get(wl,'#333'))
        
    if not labels:
        print("      -> Skipping: No context switch data found.")
        plt.close(fig); return

    ax.bar(range(len(labels)), means, yerr=[err_lo,err_hi], capsize=6,
           color=cols, alpha=0.8, edgecolor='black', linewidth=0.8)
    ax.set_xticks(range(len(labels))); ax.set_xticklabels(labels)
    ax.set_ylabel("Total Context Switches")
    ax.set_title("Context Switches by Workload (mean ± 95% CI)")
    ax.grid(True, alpha=0.3, axis='y')
    fig.savefig(os.path.join(PLOTS_DIR, "fig3_context_switches.png")); plt.close(fig)


def plot_fairness():
    print("  [4] Fairness Index...")
    fig, ax = plt.subplots(figsize=(8,5))
    labels, means, err_lo, err_hi, cols = [],[],[],[],[]
    
    for wl in ["cpu","io","mixed","starvation"]:
        data = load_flat_csv(os.path.join(DATA_DIR, f"main_{wl}_paaqpa.csv"))
        if not data: continue
            
        m = extract_trial_metrics([data])
        f = m.get("fairness",[])
        if not f: continue
            
        mean,lo,hi = mean_ci(f)
        labels.append(wl.capitalize()); means.append(mean)
        err_lo.append(mean-lo); err_hi.append(hi-mean)
        cols.append(COLORS.get(wl,'#333'))
        
    if not labels:
        print("      -> Skipping: No fairness data found.")
        plt.close(fig); return

    ax.bar(range(len(labels)), means, yerr=[err_lo,err_hi], capsize=6,
           color=cols, alpha=0.8, edgecolor='black', linewidth=0.8)
    ax.set_xticks(range(len(labels))); ax.set_xticklabels(labels)
    ax.set_ylabel("Jain's Fairness Index"); ax.set_ylim(0,1.1)
    ax.axhline(y=1.0, color='gray', linestyle='--', alpha=0.5, label='Perfect fairness')
    ax.set_title("Fairness by Workload (mean ± 95% CI)")
    ax.legend(); ax.grid(True, alpha=0.3, axis='y')
    fig.savefig(os.path.join(PLOTS_DIR, "fig4_fairness.png")); plt.close(fig)


def plot_process_scaling():
    print("  [5] Process Scaling...")
    p = os.path.join(DATA_DIR, "scaling_mixed.csv")
    if not os.path.exists(p):
        print("      -> Skipping: scaling_mixed.csv not found.")
        return
        
    data = defaultdict(lambda: defaultdict(list))
    with open(p) as f:
        for row in csv.DictReader(f):
            n = int(row.get("nproc", row.get("processes", 0)))
            if n == 0: continue
            if "avg_wait" in row: data[n]["avg_wait"].append(float(row["avg_wait"]))
            if "ctx_switches" in row: data[n]["ctx_switches"].append(float(row["ctx_switches"]))
            if "fairness" in row: data[n]["fairness"].append(float(row["fairness"]))
            
    nprocs = sorted(data.keys())
    if not nprocs or not data[nprocs[0]]["avg_wait"]:
        print("      -> Skipping: Incomplete scaling data.")
        return

    fig, axes = plt.subplots(1,3,figsize=(16,5))
    for idx, (metric, ylabel, color, marker) in enumerate([
        ("avg_wait","Avg Waiting Time",'#3498db','o'),
        ("ctx_switches","Context Switches",'#e74c3c','s'),
        ("fairness","Jain's Fairness",'#2ecc71','D')]):
        
        ms = [mean_ci(data[n][metric]) for n in nprocs]
        axes[idx].errorbar(nprocs, [m[0] for m in ms],
                           yerr=[[m[0]-m[1] for m in ms],[m[2]-m[0] for m in ms]],
                           fmt=f'{marker}-', color=color, linewidth=2, markersize=7, capsize=5)
        axes[idx].set_xlabel("Number of Processes"); axes[idx].set_ylabel(ylabel)
        axes[idx].set_title(f"{ylabel} vs Process Count"); axes[idx].grid(True, alpha=0.3)
        if metric == "fairness": axes[idx].set_ylim(0,1.1)
        
    fig.suptitle("PA-AQPA Scaling with Process Count (Mixed Workload)", fontsize=15, y=1.02)
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS_DIR, "fig5_process_scaling.png")); plt.close(fig)


def plot_param_sensitivity():
    print("  [6] Parameter Sensitivity...")
    p = os.path.join(DATA_DIR, "alpha_quantum_heatmap.csv")
    if not os.path.exists(p):
        print("      -> Skipping: alpha_quantum_heatmap.csv not found.")
        return
        
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    with open(p) as f:
        for row in csv.DictReader(f):
            a = float(row.get("alpha", 0))
            q = int(row.get("max_quantum", row.get("quantum", 0)))
            if "avg_wait" in row: data[a][q]["avg_wait"].append(float(row["avg_wait"]))
            if "ctx_switches" in row: data[a][q]["ctx_switches"].append(float(row["ctx_switches"]))
            if "fairness" in row: data[a][q]["fairness"].append(float(row["fairness"]))
            if "cpu_util" in row: data[a][q]["cpu_util"].append(float(row["cpu_util"]))

    alphas = sorted(data.keys()); quanta = sorted(set(q for a in data for q in data[a]))
    if not alphas or not quanta:
        print("      -> Skipping: Missing alpha/quantum data.")
        return

    # Fig 6c: Heatmap
    fig, ax = plt.subplots(figsize=(8,6))
    heat = []
    for a in alphas:
        row = []
        for q in quanta:
            vals = data[a][q]["avg_wait"]
            row.append(sum(vals)/len(vals) if vals else float('nan'))
        heat.append(row)
        
    im = ax.imshow(heat, aspect='auto', cmap='RdYlGn_r', origin='lower')
    ax.set_xticks(range(len(quanta))); ax.set_xticklabels(quanta)
    ax.set_yticks(range(len(alphas))); ax.set_yticklabels([f"{a:.2f}" for a in alphas])
    ax.set_xlabel("MAX_QUANTUM"); ax.set_ylabel("EWMA Alpha (α)")
    ax.set_title("Avg Waiting Time Heatmap (α × MAX_QUANTUM)")
    cb = fig.colorbar(im, ax=ax); cb.set_label("Avg Waiting Time")
    
    for i in range(len(alphas)):
        for j in range(len(quanta)):
            if not math.isnan(heat[i][j]):
                ax.text(j, i, f"{heat[i][j]:.0f}", ha="center", va="center",
                        fontsize=9, fontweight='bold',
                        color="white" if heat[i][j] > max(max(r) for r in heat if not math.isnan(max(r)))*0.55 else "black")
                        
    fig.savefig(os.path.join(PLOTS_DIR, "fig6c_param_heatmap.png")); plt.close(fig)


def plot_burst_distributions():
    print("  [7] Burst Distributions...")
    data = defaultdict(lambda: defaultdict(list))
    dists = ["uniform", "exponential", "bimodal"]
    
    has_data = False
    for d in dists:
        p = os.path.join(DATA_DIR, f"burst_{d}_paaqpa.csv")
        if not os.path.exists(p): continue
        
        with open(p) as f:
            for row in csv.DictReader(f):
                has_data = True
                if "avg_wait" in row: data[d]["avg_wait"].append(float(row["avg_wait"]))
                if "ctx_switches" in row: data[d]["ctx_switches"].append(float(row["ctx_switches"]))
                if "fairness" in row: data[d]["fairness"].append(float(row["fairness"]))
                
    if not has_data:
        print("      -> Skipping: Burst distribution data not found.")
        return

    fig, axes = plt.subplots(1,3,figsize=(14,5))
    x = range(len(dists))
    
    for idx, (metric, ylabel) in enumerate([("avg_wait","Avg Waiting Time"),
                                             ("ctx_switches","Context Switches"),
                                             ("fairness","Jain's Fairness")]):
        ms = [mean_ci(data[d][metric]) if data[d][metric] else (0,0,0) for d in dists]
        axes[idx].bar(x, [m[0] for m in ms],
                      yerr=[[m[0]-m[1] for m in ms],[m[2]-m[0] for m in ms]],
                      capsize=5, color=COLOR_LIST[:len(dists)], alpha=0.8, edgecolor='black')
        axes[idx].set_xticks(x); axes[idx].set_xticklabels(dists, rotation=15)
        axes[idx].set_ylabel(ylabel); axes[idx].set_title(f"{ylabel} by Distribution")
        axes[idx].grid(True, alpha=0.3, axis='y')
        if metric == "fairness": axes[idx].set_ylim(0,1.1)
        
    fig.suptitle("PA-AQPA Across Burst Distributions", fontsize=15, y=1.02)
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS_DIR, "fig7_burst_distributions.png")); plt.close(fig)


def run_stats():
    print("  [8] Statistical Tests...")
    results = {}
    for wl in ["cpu","io","mixed","starvation"]:
        data = load_flat_csv(os.path.join(DATA_DIR, f"main_{wl}_paaqpa.csv"))
        if data:
            results[wl] = extract_trial_metrics([data])

    if not results:
        print("      -> Skipping: No data found to run statistics.")
        return

    print(f"\n  {'Comparison':<25} {'t-stat':>8} {'p-value':>10} {'Sig':>5} {'Cohen d':>8}")
    print("  " + "-"*58)
    stat_rows = []
    keys = list(results.keys())
    
    for i in range(len(keys)):
        for j in range(i+1, len(keys)):
            a = results[keys[i]].get("avg_wait",[])
            b = results[keys[j]].get("avg_wait",[])
            if len(a) >= 2 and len(b) >= 2:
                t, p = scipy_stats.ttest_ind(a, b, equal_var=False)
                ps = math.sqrt((sum((x-sum(a)/len(a))**2 for x in a)+sum((x-sum(b)/len(b))**2 for x in b))/(len(a)+len(b)-2))
                d = abs(sum(a)/len(a)-sum(b)/len(b))/ps if ps > 0 else 0
                sig = "***" if p<0.001 else "**" if p<0.01 else "*" if p<0.05 else "ns"
                lbl = f"{keys[i]} vs {keys[j]}"
                print(f"  {lbl:<25} {t:>8.3f} {p:>10.6f} {sig:>5} {d:>8.3f}")
                stat_rows.append({"comparison":lbl,"t_stat":f"{t:.4f}","p_value":f"{p:.6f}","sig":sig,"cohens_d":f"{d:.4f}"})

    all_g = [results[k].get("avg_wait",[]) for k in keys if results[k].get("avg_wait")]
    if len(all_g) >= 3:
        f_s, p_a = scipy_stats.f_oneway(*all_g)
        print(f"\n  ANOVA: F={f_s:.3f}, p={p_a:.6f}")

    if stat_rows:
        sp = os.path.join(PLOTS_DIR, "statistical_tests.csv")
        with open(sp,'w',newline='') as f:
            w = csv.DictWriter(f, fieldnames=stat_rows[0].keys()); w.writeheader(); w.writerows(stat_rows)


def print_summary():
    print("\n  ╔══════════════════════════════════════════════════════════════════════════╗")
    print("  ║                   PA-AQPA RESULTS SUMMARY                                ║")
    print("  ╠══════════════════════════════════════════════════════════════════════════╣")
    print(f"  ║ {'Workload':<12} {'AvgWait':>9} {'AvgTurn':>9} {'AvgResp':>9} {'CS':>7} {'Fair':>7} ║")
    print("  ╠══════════════════════════════════════════════════════════════════════════╣")
    
    found_data = False
    for wl in ["cpu","io","mixed","starvation"]:
        data = load_flat_csv(os.path.join(DATA_DIR, f"main_{wl}_paaqpa.csv"))
        if not data: continue
            
        found_data = True
        m = extract_trial_metrics([data])
        w,_,_ = mean_ci(m.get("avg_wait",[0]))
        t,_,_ = mean_ci(m.get("avg_turnaround",[0]))
        r,_,_ = mean_ci(m.get("avg_response",[0]))
        c,_,_ = mean_ci(m.get("total_cs",[0]))
        f,_,_ = mean_ci(m.get("fairness",[1]))
        print(f"  ║ {wl:<12} {w:>9.1f} {t:>9.1f} {r:>9.1f} {c:>7.0f} {f:>7.4f} ║")
        
    if not found_data:
        print("  ║                          NO DATA FOUND                                   ║")
        
    print("  ╚══════════════════════════════════════════════════════════════════════════╝")


def main():
    print("="*60)
    print("  PA-AQPA Analysis Pipeline")
    print("="*60)
    print_summary()
    plot_wait_cdf()
    plot_turnaround_box()
    plot_context_switches()
    plot_fairness()
    plot_process_scaling()
    plot_param_sensitivity()
    plot_burst_distributions()
    run_stats()
    print(f"\n  All plots saved to {PLOTS_DIR}/")
    print("  Analysis complete!")

if __name__ == "__main__":
    main()
