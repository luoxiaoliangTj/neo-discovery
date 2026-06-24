/**
 * NEO Discovery Dashboard — Main Application
 * 
 * Interactive visualization of Near-Earth Object data:
 * - Orbital plot (scatter)
 * - Close approaches timeline
 * - Confidence-rated candidate tracker
 * - Discovery statistics chart
 */

// ========== CONFIGURATION ==========
const CONFIG = {
    colors: {
        apollo: '#3b82f6',
        amor: '#06b6d4',
        aten: '#8b5cf6',
        ieo: '#f59e0b',
        pha: '#ef4444',
        sun: '#fbbf24',
        earth: '#22c55e',
        grid: 'rgba(255, 255, 255, 0.03)',
        gridMajor: 'rgba(255, 255, 255, 0.06)',
        text: '#94a3b8',
        textMuted: '#64748b'
    },
    orbitColors: ['#3b82f6', '#06b6d4', '#8b5cf6', '#f59e0b'],
    maxApproachDistLD: 40,
    animationDuration: 800
};

// ========== DATA ==========
let DASHBOARD_DATA = null;

// ========== GLOBAL ERROR HANDLER ==========
window.addEventListener('error', (event) => {
    const el = document.getElementById('last-update');
    if (el) el.textContent = 'JS Error: ' + event.message;
});

// ========== INITIALIZATION ==========
document.addEventListener('DOMContentLoaded', () => {
    try {
        if (typeof generateDashboardData !== 'function') {
            console.error('generateDashboardData() not found — data.js failed to load');
            const el = document.getElementById('last-update');
            if (el) el.textContent = 'Error: data.js failed to load';
            showFallbackUI();
            return;
        }
        DASHBOARD_DATA = generateDashboardData();
        document.body.classList.add('loaded');
        initHeaderStats();
        initApproachesList();
        initCandidatesList();
        initDiscoveryTimeline();
        initDiscoveryChart();
        initFilters();
        startLiveUpdates();
        // Fallback: ensure all skeletons removed after 3s
        setTimeout(() => {
            document.body.classList.add('loaded');
            document.querySelectorAll('.loading-skeleton').forEach(el => el.remove());
        }, 3000);
    } catch (e) {
        console.error('Dashboard init error:', e);
        const el = document.getElementById('last-update');
        if (el) el.textContent = 'Init error: ' + e.message;
        showFallbackUI();
    }
});

function showFallbackUI() {
    // Remove all loading skeletons and show static data from HTML
    document.querySelectorAll('.loading-skeleton').forEach(el => {
        el.textContent = 'Data loading failed — check connection';
        el.style.color = '#ef4444';
    });
    // Show what we can from the HTML skeleton
    const statValues = document.querySelectorAll('.stat-value');
    statValues.forEach(el => {
        if (el.textContent === '—') {
            el.textContent = '—';
        }
    });
    document.body.classList.add('loaded');
}

// ========== HEADER STATS ==========
function initHeaderStats() {
    try {
        const d = DASHBOARD_DATA;
        if (!d || !d.catalog) throw new Error('DASHBOARD_DATA.catalog is missing');
        
        setStatValue('stat-total', d.catalog.totalNEOs.toLocaleString());
        setStatValue('stat-candidates', d.candidates.length);
        setStatValue('stat-approaches', d.approaches.length);
        setStatValue('stat-pha', d.catalog.phaCount.toLocaleString());
        
        setStatValue('stat-2026', d.catalog.newThisYear.toLocaleString());
        setStatValue('stat-early', d.stats.earlyDiscoveries);
        setStatValue('stat-high-conf', d.stats.highConfidenceCandidates);
        setStatValue('stat-avg-lead', d.stats.avgLeadTimeDays > 0 ? d.stats.avgLeadTimeDays.toFixed(1) : 'N/A');
        
        const updateTime = new Date(d.meta.lastUpdate);
        document.getElementById('last-update').textContent = 
            `Last update: ${updateTime.toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })}`;
    } catch (e) {
        console.error('initHeaderStats error:', e);
    }
}

function setStatValue(id, value) {
    const el = document.getElementById(id);
    if (el) {
        animateNumber(el, value);
    }
}

function animateNumber(el, target) {
    if (target === 'N/A' || target === '—') {
        el.textContent = target;
        return;
    }
    const isInt = !String(target).includes('.');
    const numTarget = parseFloat(target.replace(/,/g, ''));
    const start = 0;
    const duration = 1200;
    const startTime = performance.now();
    
    function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        const current = start + (numTarget - start) * eased;
        
        el.textContent = isInt ? Math.round(current).toLocaleString() : current.toFixed(1);
        
        if (progress < 1) {
            requestAnimationFrame(update);
        } else {
            el.textContent = typeof target === 'string' ? target : numTarget.toLocaleString();
        }
    }
    
    requestAnimationFrame(update);
}

// ========== (Orbit plot removed — static HTML card) ==========

// ========== APPROACHES LIST ==========
function initApproachesList() {
    const container = document.getElementById('approaches-list');
    if (!container) return;

    try {
        const approaches = DASHBOARD_DATA.approaches || [];
        container.innerHTML = '';
        if (approaches.length === 0) {
            container.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--text-muted);">No upcoming close approaches in next 7 days</div>';
            return;
        }
        
        approaches.forEach((a, i) => {
            const maxDist = CONFIG.maxApproachDistLD;
            const pct = Math.max(5, Math.min(100, ((maxDist - a.distLD) / maxDist) * 100));
            
            const el = document.createElement('div');
            el.className = `approach-item ${a.pha ? 'pha' : ''}`;
            el.style.animationDelay = `${i * 50}ms`;
            
            el.innerHTML = `
                <div class="approach-info">
                    <span class="approach-name">${a.name}</span>
                    <span class="approach-date">${a.date}</span>
                </div>
                <div class="approach-distance-bar">
                    <div class="approach-distance-fill" style="width: 0%"></div>
                </div>
                <div class="approach-distance">${a.distLD.toFixed(1)} LD</div>
                <div class="approach-velocity">${a.vel} km/s</div>
            `;
            
            container.appendChild(el);
            
            // Animate bar
            setTimeout(() => {
                el.querySelector('.approach-distance-fill').style.width = `${pct}%`;
            }, 100 + i * 30);
        });
    } catch (e) {
        console.error('initApproachesList error:', e);
        container.innerHTML = '<div style="padding:1rem;color:var(--text-muted);">Error loading approaches</div>';
    }
}

// ========== CANDIDATES LIST ==========
function initCandidatesList() {
    renderCandidates('all');
}

function renderCandidates(filter) {
    const container = document.getElementById('candidates-list');
    if (!container) return;
    
    try {
        let candidates = DASHBOARD_DATA.candidates || [];
        
        if (filter === 'high') {
            candidates = candidates.filter(c => c.confidence >= 70);
        } else if (filter === 'long') {
            candidates = candidates.filter(c => c.arc >= 3);
        }
        
        container.innerHTML = '';
        
        candidates.forEach((c, i) => {
            const circumference = 2 * Math.PI * 20;
            const offset = circumference - (c.confidence / 100) * circumference;
            const hue = c.confidence >= 70 ? '#3b82f6' : c.confidence >= 50 ? '#f59e0b' : '#64748b';
            
            const el = document.createElement('div');
            el.className = 'candidate-item';
            el.style.animationDelay = `${i * 30}ms`;
            
            el.innerHTML = `
                <div class="candidate-confidence">
                    <svg width="48" height="48" viewBox="0 0 48 48">
                        <circle class="conf-bg" cx="24" cy="24" r="20"/>
                        <circle class="conf-fill" cx="24" cy="24" r="20" 
                            stroke="${hue}"
                            stroke-dasharray="${circumference}"
                            stroke-dashoffset="${circumference}"/>
                    </svg>
                    <span class="conf-value" style="color: ${hue}">${c.confidence}%</span>
                </div>
                <span class="candidate-id">${c.id}</span>
                <div class="candidate-details">
                    <div class="candidate-meta">
                        <span>📏 ${c.arc}d arc</span>
                        <span>🔭 ${c.obs} obs</span>
                        <span>✨ mag ${c.mag}</span>
                        <span>📍 ${c.observer}</span>
                    </div>
                </div>
                <span class="candidate-status ${c.confidence >= 70 ? 'status-high' : 'status-pending'}">
                    ${c.confidence >= 70 ? '★ High' : '⏳ Pending'}
                </span>
            `;
            
            container.appendChild(el);
            
            // Animate confidence ring
            setTimeout(() => {
                const fill = el.querySelector('.conf-fill');
                if (fill) fill.style.strokeDashoffset = offset;
            }, 100 + i * 20);
        });
        
        if (candidates.length === 0) {
            container.innerHTML = '<div class="loading-skeleton" style="height: 60px">No candidates match this filter</div>';
        }
    } catch (e) {
        console.error('renderCandidates error:', e);
        container.innerHTML = '<div style="padding:1rem;color:var(--text-muted);">Error loading candidates</div>';
    }
}

function initFilters() {
    const buttons = document.querySelectorAll('.filter-btn');
    buttons.forEach(btn => {
        btn.addEventListener('click', () => {
            buttons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderCandidates(btn.dataset.filter);
        });
    });
}

// ========== DISCOVERY TIMELINE ==========
function initDiscoveryTimeline() {
    const container = document.getElementById('discovery-timeline');
    if (!container) return;
    
    try {
        const discoveries = DASHBOARD_DATA.earlyDiscoveries || [];
        
        if (discoveries.length === 0) {
            container.innerHTML = `
                <div style="text-align: center; padding: 2rem; color: var(--text-muted);">
                    <div style="font-size: 2rem; margin-bottom: 0.5rem;">🔭</div>
                    <p style="font-size: 0.875rem;">Tracking active — monitoring ${DASHBOARD_DATA.candidates.length} candidates</p>
                    <p style="font-size: 0.75rem; margin-top: 0.5rem;">Early discoveries will appear here when a candidate receives its NASA designation</p>
                </div>`;
            return;
        }
        
        container.innerHTML = '';
        discoveries.forEach(d => {
            const el = document.createElement('div');
            el.className = 'timeline-item';
            el.innerHTML = `
                <div class="timeline-date">${d.date}</div>
                <div class="timeline-content">
                    <div class="timeline-name">${d.name}</div>
                    <div class="timeline-lead">Detected ${d.leadDays} days before NASA catalog</div>
                </div>
            `;
            container.appendChild(el);
        });
    } catch (e) {
        console.error('initDiscoveryTimeline error:', e);
    }
}

// ========== DISCOVERY CHART ==========
let discoveryChart = null;

function initDiscoveryChart() {
    const canvas = document.getElementById('discovery-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    
    try {
        const ctx = canvas.getContext('2d');
        const data = DASHBOARD_DATA.stats.discoveryByYear;
        const total = data.data.reduce((a, b) => a + b, 0);
        
        // Only show top 5 years + Other to keep chart compact
        const topN = 5;
        const labels = data.labels.slice(0, topN);
        const values = data.data.slice(0, topN);
        const otherVal = data.data.slice(topN).reduce((a, b) => a + b, 0);
        
        labels.push('Other');
        values.push(otherVal);
        
        const colors = ['#3b82f6', '#06b6d4', '#8b5cf6', '#f59e0b', '#ef4444', '#64748b'];
        
        new Chart(ctx, {
            type: 'pie',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: colors,
                    borderColor: '#1a1f35',
                    borderWidth: 2,
                    hoverOffset: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: { padding: 2 },
                plugins: {
                    legend: {
                        display: true,
                        position: 'bottom',
                        labels: {
                            color: '#94a3b8',
                            font: { size: 9 },
                            boxWidth: 8,
                            boxHeight: 8,
                            padding: 3,
                            generateLabels: function(chart) {
                                const dataset = chart.data.datasets[0];
                                return chart.data.labels.map((label, i) => {
                                    const value = dataset.data[i];
                                    const pct = ((value / total) * 100).toFixed(1);
                                    return {
                                        text: `${label} ${pct}%`,
                                        fillStyle: dataset.backgroundColor[i],
                                        strokeStyle: dataset.backgroundColor[i],
                                        lineWidth: 0,
                                        hidden: false,
                                        index: i,
                                        pointStyle: 'circle'
                                    };
                                });
                            }
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(ctx) {
                                const val = ctx.parsed;
                                const pct = ((val / total) * 100).toFixed(1);
                                return `${ctx.label}: ${val.toLocaleString()} (${pct}%)`;
                            }
                        }
                    }
                }
            }
        });
    } catch (e) {
        console.error('initDiscoveryChart error:', e);
    }
}

// ========== LIVE UPDATES (Orbit animation) ==========
function startLiveUpdates() {
    // The orbit plot already animates via requestAnimationFrame
    // This handles any periodic data refresh needs
    setInterval(() => {
        // Pulse effect for status dot
        const dot = document.getElementById('status-dot');
        if (dot) {
            dot.style.background = dot.style.background === 'rgb(16, 185, 129)' ? '#10b981' : 'rgb(16, 185, 129)';
        }
    }, 1500);
}

// ========== UTILITY: Handle resize ==========
window.addEventListener('resize', () => {
    // Orbit plot redraws automatically via requestAnimationFrame
    // Chart.js handles its own resize
});
