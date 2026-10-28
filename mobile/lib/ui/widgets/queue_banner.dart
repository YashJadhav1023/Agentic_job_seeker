import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../state/providers.dart';
import '../theme.dart';

/// "2 running · 3 waiting (3 at a time)". Hidden when nothing runs or waits. Refreshes itself every few seconds.
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
    _t = Timer.periodic(const Duration(seconds: 6), (_) {
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
    final max = q['max_parallel'] ?? 3;
    String kind(dynamic r) => '${(r as Map)['kind']}'.replaceAll('-', ' ');
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: widget.onTap ?? () => context.go('/runs'),
        child: Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: AppTheme.card,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: AppTheme.primary.withValues(alpha: 0.35)),
          ),
          child: Row(
            children: [
              const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('${running.length} running · ${waiting.length} waiting  (max $max at a time)',
                        style: const TextStyle(fontWeight: FontWeight.w700, color: AppTheme.textPrimary, fontSize: 13.5)),
                    const SizedBox(height: 2),
                    Text(
                      [
                        if (running.isNotEmpty) 'Now: ${running.map(kind).join(', ')}',
                        if (waiting.isNotEmpty) 'Next: ${waiting.take(3).map(kind).join(', ')}${waiting.length > 3 ? ' …' : ''}',
                      ].join('   '),
                      style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ),
              ),
              const Icon(Icons.chevron_right_rounded, color: AppTheme.textMuted),
            ],
          ),
        ),
      ),
    );
  }
}
