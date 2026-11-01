import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../state/providers.dart';
import '../theme.dart';

/// Concurrency & Queue monitor: shows active slot utilization up to MAX_PARALLEL (6 slots).
/// Refreshes itself every 4 seconds when tasks are active.
class QueueBanner extends ConsumerStatefulWidget {
  const QueueBanner({super.key, this.onTap});
  final VoidCallback? onTap;

  @override
  ConsumerState<QueueBanner> createState() => _QueueBannerState();
}

class _QueueBannerState extends ConsumerState<QueueBanner> {
  Timer? _t;

  @override
  void initState() {
    super.initState();
    _t = Timer.periodic(const Duration(seconds: 4), (_) {
      if (mounted) ref.invalidate(queueProvider);
    });
  }

  @override
  void dispose() {
    _t?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final q = ref.watch(queueProvider).value;
    if (q == null) return const SizedBox.shrink();
    final running = (q['running'] as List?) ?? const [];
    final waiting = (q['queued'] as List?) ?? const [];
    if (running.isEmpty && waiting.isEmpty) return const SizedBox.shrink();
    final maxSlots = (q['max_parallel'] as num?)?.toInt() ?? 6;
    String kind(dynamic r) => '${(r as Map)['kind']}'.replaceAll('-', ' ');

    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: widget.onTap ?? () => context.go('/runs'),
        child: Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            gradient: LinearGradient(
              colors: [
                AppTheme.card,
                AppTheme.primaryContainer.withValues(alpha: 0.35),
              ],
              begin: AlignmentDirectional.topStart,
              end: AlignmentDirectional.bottomEnd,
            ),
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: AppTheme.primary.withValues(alpha: 0.4), width: 1.2),
            boxShadow: [
              BoxShadow(
                color: AppTheme.primary.withValues(alpha: 0.1),
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
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: AppTheme.primary.withValues(alpha: 0.2),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: const Icon(Icons.bolt_rounded, size: 16, color: AppTheme.secondaryLight),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Text(
                              '${running.length} Active · ${waiting.length} Waiting',
                              style: const TextStyle(fontWeight: FontWeight.w700, color: AppTheme.textPrimary, fontSize: 14),
                            ),
                            const Spacer(),
                            Container(
                              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                              decoration: BoxDecoration(
                                color: AppTheme.primary.withValues(alpha: 0.25),
                                borderRadius: BorderRadius.circular(6),
                                border: Border.all(color: AppTheme.primary.withValues(alpha: 0.4)),
                              ),
                              child: Text(
                                'Engine: $maxSlots Slots',
                                style: const TextStyle(
                                  color: AppTheme.primaryLight,
                                  fontSize: 12,
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 4),
                        Text(
                          [
                            if (running.isNotEmpty) 'Active: ${running.map(kind).join(', ')}',
                            if (waiting.isNotEmpty) 'Next: ${waiting.take(3).map(kind).join(', ')}${waiting.length > 3 ? ' …' : ''}',
                          ].join('   ·   '),
                          style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 8),
                  const Icon(Icons.chevron_right_rounded, color: AppTheme.textMuted, size: 20),
                ],
              ),
              const SizedBox(height: 12),
              // Slot Utilization Meter (6 parallel slots)
              Row(
                children: [
                  for (int i = 0; i < maxSlots; i++)
                    Expanded(
                      child: Container(
                        height: 6,
                        margin: EdgeInsetsDirectional.only(end: i < maxSlots - 1 ? 4 : 0),
                        decoration: BoxDecoration(
                          color: i < running.length
                              ? AppTheme.secondaryLight
                              : (i < running.length + waiting.length
                                  ? AppTheme.warningLight.withValues(alpha: 0.6)
                                  : AppTheme.surface),
                          borderRadius: BorderRadius.circular(3),
                        ),
                      ),
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
