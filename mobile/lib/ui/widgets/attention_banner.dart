import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../state/providers.dart';
import '../screens/tool_run_screen.dart' show requireClientFrom;
import '../theme.dart';

/// Interactive Human Interface: prompts the user when a career portal requires
/// account creation, sign-in, or a security verification check.
class AttentionBanner extends ConsumerStatefulWidget {
  const AttentionBanner({super.key});

  @override
  ConsumerState<AttentionBanner> createState() => _AttentionBannerState();
}

class _AttentionBannerState extends ConsumerState<AttentionBanner> {
  Timer? _t;

  @override
  void initState() {
    super.initState();
    _t = Timer.periodic(const Duration(seconds: 6), (_) {
      if (mounted) ref.invalidate(attentionProvider);
    });
  }

  @override
  void dispose() {
    _t?.cancel();
    super.dispose();
  }

  Future<void> _skip(int id) async {
    try {
      await requireClientFrom(ref).call('attention_resolve', {'id': id, 'status': 'skipped'});
      ref.invalidate(attentionProvider);
    } catch (_) {}
  }

  Future<void> _confirmResolved(int id) async {
    try {
      // Mark as done so the bot proceeds immediately without waiting for poll timeout
      await requireClientFrom(ref).call('attention_resolve', {'id': id, 'status': 'done'});
      ref.invalidate(attentionProvider);
      ref.invalidate(runsProvider);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Verification confirmed! AI bot is resuming application submission.')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Error: $e')));
      }
    }
  }

  void _showGuidanceModal(Map<String, dynamic> r) {
    final site = '${r['site'] ?? 'Company Portal'}';
    final job = '${r['job'] ?? ''}';
    final kind = '${r['kind'] ?? 'login'}';
    final openUrl = '${r['open_url'] ?? ''}';
    final id = r['id'] as int;

    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppTheme.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (ctx) => Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: AppTheme.warning.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: const Icon(Icons.person_pin_rounded, color: AppTheme.warningLight, size: 22),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        kind == 'login' ? 'Sign In / Account Required' : 'Security Check Required',
                        style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16, color: AppTheme.textPrimary),
                      ),
                      Text(site, style: const TextStyle(fontSize: 12.5, color: AppTheme.secondaryLight, fontWeight: FontWeight.w600)),
                    ],
                  ),
                ),
                IconButton(icon: const Icon(Icons.close_rounded), onPressed: () => Navigator.pop(ctx)),
              ],
            ),
            const SizedBox(height: 16),
            if (job.isNotEmpty) ...[
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: AppTheme.card,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: AppTheme.borderSubtle),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Target Position:', style: TextStyle(fontSize: 11.5, color: AppTheme.textMuted, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 2),
                    Text(job, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: AppTheme.textPrimary)),
                  ],
                ),
              ),
              const SizedBox(height: 16),
            ],
            const Text(
              'How it works:',
              style: TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5, color: AppTheme.textPrimary),
            ),
            const SizedBox(height: 8),
            _buildStep('1', 'Tap "Open Sign-In Browser" below to access the secure live browser session.'),
            _buildStep('2', 'Create your account or log in with your credentials, or solve the captcha.'),
            _buildStep('3', 'Return here and tap "I Have Logged In" — the AI bot will automatically resume and complete your application.'),
            const SizedBox(height: 20),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () {
                      Navigator.pop(ctx);
                      _skip(id);
                    },
                    child: const Text('Skip Job'),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  flex: 2,
                  child: FilledButton.icon(
                    icon: const Icon(Icons.open_in_browser_rounded, size: 18),
                    label: const Text('Open Browser'),
                    onPressed: openUrl.isEmpty
                        ? null
                        : () {
                            launchUrl(Uri.parse(openUrl), mode: LaunchMode.externalApplication);
                          },
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            SizedBox(
              width: double.infinity,
              child: FilledButton.tonalIcon(
                icon: const Icon(Icons.check_circle_outline_rounded, size: 18),
                label: const Text("I've Logged In — Continue Application"),
                onPressed: () {
                  Navigator.pop(ctx);
                  _confirmResolved(id);
                },
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildStep(String num, String text) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 18,
            height: 18,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: AppTheme.primaryContainer,
              shape: BoxShape.circle,
              border: Border.all(color: AppTheme.primaryLight, width: 1),
            ),
            child: Text(num, style: const TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: AppTheme.primaryLight)),
          ),
          const SizedBox(width: 8),
          Expanded(child: Text(text, style: const TextStyle(fontSize: 12.5, color: AppTheme.textSecondary))),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final items = ref.watch(attentionProvider).value ?? const [];
    if (items.isEmpty) return const SizedBox.shrink();

    return Column(
      children: [
        for (final r in items)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: AppTheme.card,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: AppTheme.warning.withValues(alpha: 0.5), width: 1.2),
                boxShadow: [
                  BoxShadow(
                    color: AppTheme.warning.withValues(alpha: 0.08),
                    blurRadius: 10,
                    offset: const Offset(0, 3),
                  ),
                ],
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Container(
                        padding: const EdgeInsets.all(6),
                        decoration: BoxDecoration(
                          color: AppTheme.warning.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: const Icon(Icons.front_hand_rounded, color: AppTheme.warningLight, size: 18),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              r['kind'] == 'login'
                                  ? 'Account / Login Required: ${r['site']}'
                                  : 'Human Verification: ${r['site']}',
                              style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5, color: AppTheme.textPrimary),
                            ),
                            if ('${r['job'] ?? ''}'.isNotEmpty)
                              Text(
                                '${r['job']}',
                                style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12),
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                              ),
                          ],
                        ),
                      ),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
                        decoration: BoxDecoration(
                          color: AppTheme.warning.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: Text(
                          '${r['minutes_left'] ?? '15'}m left',
                          style: const TextStyle(color: AppTheme.warningLight, fontSize: 11, fontWeight: FontWeight.w700),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.end,
                    children: [
                      TextButton(
                        onPressed: () => _skip(r['id'] as int),
                        child: const Text('Skip', style: TextStyle(color: AppTheme.textMuted)),
                      ),
                      const SizedBox(width: 8),
                      OutlinedButton.icon(
                        icon: const Icon(Icons.info_outline_rounded, size: 16),
                        label: const Text('Help & Steps'),
                        onPressed: () => _showGuidanceModal(r),
                      ),
                      const SizedBox(width: 8),
                      FilledButton.icon(
                        icon: const Icon(Icons.open_in_browser_rounded, size: 16),
                        label: const Text('Sign In / Register'),
                        onPressed: '${r['open_url'] ?? ''}'.isEmpty
                            ? null
                            : () => launchUrl(Uri.parse('${r['open_url']}'), mode: LaunchMode.externalApplication),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
      ],
    );
  }
}
