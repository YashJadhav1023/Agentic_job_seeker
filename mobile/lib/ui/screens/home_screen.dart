import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../state/providers.dart';
import '../theme.dart';
import '../widgets/common.dart';
import '../widgets/queue_banner.dart';
import 'runs_screen.dart' show runIsFailed, runIsRunning;
import 'tool_run_screen.dart';

/// Modern Dashboard: Hero greeting, real-time counters, autopilot control, accounts, and quick shortcuts.
class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = ref.watch(statusProvider);
    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(7),
              decoration: BoxDecoration(
                gradient: const LinearGradient(
                  colors: [AppTheme.primary, AppTheme.secondary],
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                ),
                borderRadius: BorderRadius.circular(10),
              ),
              child: const Icon(Icons.auto_awesome, size: 16, color: Colors.white),
            ),
            const SizedBox(width: 10),
            const Text('Job Seeker AI'),
          ],
        ),
        actions: [
          IconButton(
            tooltip: 'Refresh dashboard',
            icon: const Icon(Icons.refresh_rounded),
            onPressed: () => ref.invalidate(statusProvider),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: AsyncBody<Map<String, dynamic>>(
        value: status,
        onRetry: () {
          ref.invalidate(statusProvider);
          if (ref.read(clientProvider) == null) context.go('/settings');
        },
        data: (s) => RefreshIndicator(
          onRefresh: () => ref.refresh(statusProvider.future),
          child: ListView(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            children: [
              _HeroCard(s),
              const SizedBox(height: 14),
              _SetupChecklist(s),
              const QueueBanner(),
              _TodayCounters(s),
              const SizedBox(height: 14),
              _AutopilotControl(s),
              const SizedBox(height: 14),
              _PlatformAccounts(s),
              const SizedBox(height: 14),
              const _QuickActionsSection(),
              const SizedBox(height: 14),
              const _RecentActivitySection(),
              const SizedBox(height: 24),
            ],
          ),
        ),
      ),
    );
  }
}

class _HeroCard extends StatelessWidget {
  const _HeroCard(this.s);
  final Map<String, dynamic> s;

  @override
  Widget build(BuildContext context) {
    final live = s['real_actions_allowed'] == true;
    final candidate = s['candidate'] ?? 'Candidate';
    final locations = ((s['locations'] as List?) ?? const []).join(' · ');

    return Container(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [
            const Color(0xFF312E81), // Deep Indigo
            const Color(0xFF1E1B4B),
            AppTheme.card,
          ],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: AppTheme.primary.withValues(alpha: 0.3), width: 1.2),
        boxShadow: [
          BoxShadow(
            color: AppTheme.primary.withValues(alpha: 0.15),
            blurRadius: 16,
            offset: const Offset(0, 6),
          ),
        ],
      ),
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Hi, $candidate 👋',
                      style: const TextStyle(
                        fontSize: 22,
                        fontWeight: FontWeight.w800,
                        color: Colors.white,
                        letterSpacing: -0.4,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      (((s['keywords'] as List?) ?? const []).isEmpty)
                          ? 'Upload your resume to set your target roles'
                          : 'Targeting ${((s['keywords'] as List).take(3)).join(' · ')}',
                      style: TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w500,
                        color: Colors.white.withValues(alpha: 0.75),
                      ),
                    ),
                  ],
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                  color: live ? AppTheme.success.withValues(alpha: 0.2) : AppTheme.warning.withValues(alpha: 0.2),
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(
                    color: live ? AppTheme.success : AppTheme.warning,
                    width: 1,
                  ),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Container(
                      width: 6,
                      height: 6,
                      decoration: BoxDecoration(
                        color: live ? AppTheme.success : AppTheme.warning,
                        shape: BoxShape.circle,
                      ),
                    ),
                    const SizedBox(width: 6),
                    Text(
                      live ? 'LIVE ACTIVE' : 'DRY-RUN SAFE',
                      style: TextStyle(
                        color: live ? AppTheme.successLight : AppTheme.warningLight,
                        fontSize: 10.5,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 0.5,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          if (locations.isNotEmpty) ...[
            const SizedBox(height: 14),
            Row(
              children: [
                const Icon(Icons.location_on_outlined, size: 14, color: AppTheme.secondaryLight),
                const SizedBox(width: 4),
                Expanded(
                  child: Text(
                    locations,
                    style: const TextStyle(color: AppTheme.secondaryLight, fontSize: 12.5, fontWeight: FontWeight.w500),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _TodayCounters extends StatelessWidget {
  const _TodayCounters(this.s);
  final Map<String, dynamic> s;

  @override
  Widget build(BuildContext context) {
    final t = (s['today'] as Map?) ?? const {};
    final caps = (t['caps'] as Map?) ?? const {};

    return Row(
      children: [
        MetricCard(
          title: 'Applications Today',
          value: (t['applications'] as num?) ?? 0,
          cap: (caps['applications'] as num?) ?? 25,
          icon: Icons.send_rounded,
          accentColor: AppTheme.primaryLight,
        ),
        const SizedBox(width: 12),
        MetricCard(
          title: 'Emails Sent Today',
          value: (t['emails_sent'] as num?) ?? 0,
          cap: (caps['emails'] as num?) ?? 20,
          icon: Icons.mark_email_read_rounded,
          accentColor: AppTheme.secondary,
        ),
      ],
    );
  }
}

class _AutopilotControl extends ConsumerStatefulWidget {
  const _AutopilotControl(this.s);
  final Map<String, dynamic> s;

  @override
  ConsumerState<_AutopilotControl> createState() => _AutopilotControlState();
}

class _AutopilotControlState extends ConsumerState<_AutopilotControl> {
  bool _busy = false;

  @override
  Widget build(BuildContext context) {
    final ap = (widget.s['autopilot'] as Map?) ?? const {};
    final stopped = ap['stopped'] == true;
    final cooldowns = ((ap['cooldowns'] as Map?) ?? const {}).cast<String, dynamic>();

    return Container(
      decoration: BoxDecoration(
        color: AppTheme.card,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppTheme.borderSubtle),
      ),
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: AppTheme.primary.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(10),
                ),
                child: const Icon(Icons.smart_toy_rounded, size: 20, color: AppTheme.primaryLight),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Autopilot Automation',
                      style: TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w700,
                        color: AppTheme.textPrimary,
                      ),
                    ),
                    Text(
                      stopped ? 'Paused by kill switch' : 'Active and ready for next cycle',
                      style: TextStyle(
                        fontSize: 12,
                        color: stopped ? AppTheme.error : AppTheme.successLight,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ],
                ),
              ),
              Switch(
                value: !stopped,
                activeThumbColor: AppTheme.success,
                activeTrackColor: AppTheme.successContainer,
                onChanged: _busy
                    ? null
                    : (allow) async {
                        setState(() => _busy = true);
                        try {
                          await requireClientFrom(ref).call('autopilot_stop', {'on': !allow});
                          ref.invalidate(statusProvider);
                        } catch (e) {
                          if (context.mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                          }
                        } finally {
                          if (mounted) setState(() => _busy = false);
                        }
                      },
              ),
            ],
          ),
          const SizedBox(height: 12),
          const Divider(),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: FilledButton.icon(
                  style: FilledButton.styleFrom(
                    backgroundColor: AppTheme.primary,
                    padding: const EdgeInsets.symmetric(vertical: 11),
                  ),
                  onPressed: () => context.push('/tools/autopilot_run', extra: {}),
                  icon: const Icon(Icons.play_arrow_rounded, size: 18),
                  label: const Text('Start Autopilot Run'),
                ),
              ),
              const SizedBox(width: 10),
              OutlinedButton.icon(
                style: OutlinedButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11)),
                onPressed: () => context.go('/runs'),
                icon: const Icon(Icons.receipt_long_rounded, size: 16),
                label: const Text('Logs'),
              ),
            ],
          ),
          if (cooldowns.isNotEmpty) ...[
            const SizedBox(height: 10),
            for (final c in cooldowns.entries)
              Container(
                margin: const EdgeInsets.only(top: 4),
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                decoration: BoxDecoration(
                  color: AppTheme.warning.withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: AppTheme.warning.withValues(alpha: 0.3)),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.timer_outlined, size: 14, color: AppTheme.warningLight),
                    const SizedBox(width: 6),
                    Text(
                      '${c.key[0].toUpperCase()}${c.key.substring(1)} on cooldown: ${c.value}',
                      style: const TextStyle(fontSize: 11.5, color: AppTheme.warningLight, fontWeight: FontWeight.w500),
                    ),
                  ],
                ),
              ),
          ],
        ],
      ),
    );
  }
}

class _SetupChecklist extends StatelessWidget {
  const _SetupChecklist(this.s);
  final Map<String, dynamic> s;

  @override
  Widget build(BuildContext context) {
    final sessions = ((s['login_sessions_saved'] as Map?) ?? const {}).cast<String, dynamic>();
    final creds = ((s['credentials'] as Map?) ?? const {}).cast<String, dynamic>();
    final steps = <(String, bool, String)>[
      ('Resume uploaded', ((s['resume'] as Map?)?['exists']) == true, '/settings'),
      ('Profile complete', ((s['missing_profile_fields'] as List?) ?? const []).isEmpty, '/settings'),
      ('Gmail linked', creds['gmail_app_password'] == true, '/settings'),
      ('Naukri login', sessions['naukri'] == true, '/tools/login_platform'),
      ('LinkedIn login', sessions['linkedin'] == true, '/tools/login_platform'),
      ('Live actions confirmed', s['real_actions_allowed'] == true, '/settings'),
    ];
    final done = steps.where((e) => e.$2).length;
    if (done == steps.length) return const SizedBox.shrink();

    return Container(
      margin: const EdgeInsets.only(bottom: 14),
      decoration: BoxDecoration(
        color: AppTheme.card,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppTheme.borderSubtle),
      ),
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Setup Readiness ($done of ${steps.length})',
                  style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: AppTheme.textPrimary)),
              Text('${((done / steps.length) * 100).round()}%',
                  style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: AppTheme.primaryLight)),
            ],
          ),
          const SizedBox(height: 8),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: done / steps.length,
              minHeight: 6,
              backgroundColor: AppTheme.surface,
              valueColor: const AlwaysStoppedAnimation<Color>(AppTheme.primary),
            ),
          ),
          const SizedBox(height: 12),
          for (final (label, ok, route) in steps)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: [
                  Icon(
                    ok ? Icons.check_circle_rounded : Icons.radio_button_unchecked_rounded,
                    size: 16,
                    color: ok ? AppTheme.success : AppTheme.textMuted,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      label,
                      style: TextStyle(
                        fontSize: 13,
                        color: ok ? AppTheme.textSecondary : AppTheme.textPrimary,
                        decoration: ok ? TextDecoration.lineThrough : null,
                      ),
                    ),
                  ),
                  if (!ok)
                    GestureDetector(
                      onTap: () => route == '/tools/login_platform'
                          ? context.push(route, extra: {'platform': label.split(' ').first.toLowerCase()})
                          : context.go(route),
                      child: const Text(
                        'Set up →',
                        style: TextStyle(color: AppTheme.primaryLight, fontSize: 12, fontWeight: FontWeight.w600),
                      ),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _PlatformAccounts extends StatelessWidget {
  const _PlatformAccounts(this.s);
  final Map<String, dynamic> s;

  @override
  Widget build(BuildContext context) {
    final sessions = ((s['login_sessions_saved'] as Map?) ?? const {}).cast<String, dynamic>();
    final creds = ((s['credentials'] as Map?) ?? const {}).cast<String, dynamic>();

    final accounts = [
      ('LinkedIn', sessions['linkedin'] == true, Icons.business_center_rounded, 'linkedin'),
      ('Naukri', sessions['naukri'] == true, Icons.work_rounded, 'naukri'),
      ('Indeed', sessions['indeed'] == true, Icons.search_rounded, 'indeed'),
      ('Gmail SMTP', creds['gmail_app_password'] == true, Icons.mail_rounded, 'gmail'),
    ];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SectionHeader(title: 'Connected Platforms', icon: Icons.link_rounded),
        GridView.count(
          crossAxisCount: 2,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          childAspectRatio: 2.2,
          mainAxisSpacing: 10,
          crossAxisSpacing: 10,
          children: [
            for (final (name, connected, icon, id) in accounts)
              Container(
                decoration: BoxDecoration(
                  color: AppTheme.card,
                  borderRadius: BorderRadius.circular(14),
                  border: Border.all(
                    color: connected ? AppTheme.success.withValues(alpha: 0.3) : AppTheme.borderSubtle,
                  ),
                ),
                padding: const EdgeInsets.all(12),
                child: Row(
                  children: [
                    Container(
                      padding: const EdgeInsets.all(8),
                      decoration: BoxDecoration(
                        color: connected ? AppTheme.success.withValues(alpha: 0.12) : AppTheme.surface,
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: Icon(icon, size: 18, color: connected ? AppTheme.successLight : AppTheme.textMuted),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Text(name,
                              style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: AppTheme.textPrimary),
                              maxLines: 1),
                          const SizedBox(height: 2),
                          Text(
                            connected ? 'Ready' : 'Login needed',
                            style: TextStyle(
                              fontSize: 11,
                              color: connected ? AppTheme.successLight : AppTheme.warningLight,
                              fontWeight: FontWeight.w500,
                            ),
                          ),
                        ],
                      ),
                    ),
                    if (!connected)
                      IconButton(
                        visualDensity: VisualDensity.compact,
                        icon: const Icon(Icons.arrow_forward_ios_rounded, size: 12, color: AppTheme.primaryLight),
                        onPressed: () => id == 'gmail'
                            ? context.go('/settings')
                            : context.push('/tools/login_platform', extra: {'platform': id}),
                      ),
                  ],
                ),
              ),
          ],
        ),
      ],
    );
  }
}

class _QuickActionsSection extends StatelessWidget {
  const _QuickActionsSection();

  @override
  Widget build(BuildContext context) {
    final actions = <(IconData, String, Color, String, Map<String, dynamic>)>[
      (Icons.travel_explore_rounded, 'Search All Boards & Apply', AppTheme.primaryLight, '_search', {}),
      (Icons.search_rounded, 'LinkedIn Search & Apply', const Color(0xFF38BDF8), 'search_jobs', {'save': true}),
      (Icons.dynamic_feed_rounded, 'Scan Hiring Posts (bulk)', const Color(0xFFFBBF24), 'scan_hiring_posts', {'max_posts': 80}),
      (Icons.assignment_turned_in_rounded, 'Easy Apply Bot', const Color(0xFF34D399), 'apply_jobs', {}),
      (Icons.contact_mail_rounded, 'Find HR Emails', const Color(0xFFA78BFA), 'find_hr_emails', {}),
      (Icons.forward_to_inbox_rounded, 'Bulk Email HR', const Color(0xFFF472B6), 'send_hr_emails', {}),
      (Icons.chat_rounded, 'WhatsApp', const Color(0xFF25D366), '_whatsapp', {}),
      (Icons.alarm_rounded, 'Schedule (alarms)', const Color(0xFFFB923C), '_schedule', {}),
      (Icons.insights_rounded, 'Progress Report', const Color(0xFF2DD4BF), '_report', {}),
      (Icons.monitor_heart_rounded, 'System Status', AppTheme.successLight, '_status', {}),
      (Icons.grid_view_rounded, 'All Tools', AppTheme.primaryLight, '_all_tools', {}),
    ];

    void open(BuildContext context, String tool, Map<String, dynamic> args) {
      switch (tool) {
        case '_all_tools':
          context.go('/tools');
        case '_search':
          context.push('/search');
        case '_whatsapp':
          context.push('/whatsapp');
        case '_schedule':
          context.push('/schedule');
        case '_report':
          context.push('/report');
        case '_status':
          context.push('/status');
        default:
          context.push('/tools/$tool', extra: args);
      }
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SectionHeader(title: 'Quick Actions', icon: Icons.bolt_rounded),
        GridView.count(
          crossAxisCount: 2,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          childAspectRatio: 2.2,
          mainAxisSpacing: 10,
          crossAxisSpacing: 10,
          children: [
            for (final (icon, label, color, tool, args) in actions)
              InkWell(
                borderRadius: BorderRadius.circular(14),
                onTap: () => open(context, tool, args),
                child: Container(
                  decoration: BoxDecoration(
                    color: AppTheme.card,
                    borderRadius: BorderRadius.circular(14),
                    border: Border.all(color: AppTheme.borderSubtle),
                  ),
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  child: Row(
                    children: [
                      Container(
                        padding: const EdgeInsets.all(8),
                        decoration: BoxDecoration(
                          color: color.withValues(alpha: 0.12),
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: Icon(icon, size: 18, color: color),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Text(
                          label,
                          style: const TextStyle(
                            fontSize: 12.5,
                            fontWeight: FontWeight.w600,
                            color: AppTheme.textPrimary,
                          ),
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
          ],
        ),
      ],
    );
  }
}

class _RecentActivitySection extends ConsumerStatefulWidget {
  const _RecentActivitySection();

  @override
  ConsumerState<_RecentActivitySection> createState() => _RecentActivitySectionState();
}

class _RecentActivitySectionState extends ConsumerState<_RecentActivitySection> {
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 5), (_) {
      final list = ref.read(runsProvider).value;
      if (mounted && list != null && list.any(runIsRunning)) ref.invalidate(runsProvider);
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final runs = ref.watch(runsProvider).value ?? const <Map<String, dynamic>>[];
    final recent = runs.take(4).toList();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SectionHeader(
          title: 'Recent Activity',
          icon: Icons.history_rounded,
          trailing: TextButton(
            onPressed: () => context.go('/runs'),
            child: const Text('View All Logs →'),
          ),
        ),
        if (recent.isEmpty)
          Container(
            padding: const EdgeInsets.all(20),
            decoration: BoxDecoration(
              color: AppTheme.card,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: AppTheme.borderSubtle),
            ),
            child: const Center(
              child: Text('No runs recorded yet. Start one from Quick Actions above.',
                  style: TextStyle(color: AppTheme.textSecondary, fontSize: 13)),
            ),
          )
        else
          Container(
            decoration: BoxDecoration(
              color: AppTheme.card,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: AppTheme.borderSubtle),
            ),
            child: Column(
              children: [
                for (int i = 0; i < recent.length; i++) ...[
                  if (i > 0) const Divider(height: 1),
                  _ActivityTile(recent[i]),
                ],
              ],
            ),
          ),
      ],
    );
  }
}

class _ActivityTile extends StatelessWidget {
  const _ActivityTile(this.r);
  final Map<String, dynamic> r;

  @override
  Widget build(BuildContext context) {
    final running = runIsRunning(r);
    final failed = runIsFailed(r);
    final tail = ((r['log_tail'] as List?) ?? const []).map((e) => '$e').toList();
    final summary = tail.isNotEmpty ? tail.last : '${r['started']}';

    return ListTile(
      dense: true,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      leading: running
          ? const SizedBox(
              width: 20,
              height: 20,
              child: CircularProgressIndicator(strokeWidth: 2, color: AppTheme.primaryLight),
            )
          : Icon(
              failed ? Icons.error_outline_rounded : Icons.check_circle_outline_rounded,
              size: 20,
              color: failed ? AppTheme.error : AppTheme.success,
            ),
      title: Row(
        children: [
          Text(
            '${r['kind']}',
            style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5, color: AppTheme.textPrimary),
          ),
          if (r['dry_run'] == true) ...[
            const SizedBox(width: 6),
            const StatusChip('dry-run', isMini: true),
          ],
        ],
      ),
      subtitle: Text(
        summary,
        style: TextStyle(
          color: failed ? const Color(0xFFFCA5A5) : AppTheme.textSecondary,
          fontSize: 12,
        ),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: const Icon(Icons.chevron_right_rounded, size: 18, color: AppTheme.textMuted),
      onTap: () => context.push('/runs/${Uri.encodeComponent('${r['run_id']}')}'),
    );
  }
}
