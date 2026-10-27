import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../models/tool_spec.dart';
import '../../state/providers.dart';
import '../widgets/common.dart';

/// Every hub tool, grouped by what it helps with, with search.
class ToolsScreen extends ConsumerStatefulWidget {
  const ToolsScreen({super.key});

  @override
  ConsumerState<ToolsScreen> createState() => _ToolsScreenState();
}

class _ToolsScreenState extends ConsumerState<ToolsScreen> {
  String _q = '';

  static const _icons = {
    'Status': Icons.monitor_heart_outlined,
    'Discover jobs': Icons.travel_explore,
    'Apply': Icons.assignment_turned_in_outlined,
    'Autopilot': Icons.smart_toy_outlined,
    'Find contacts': Icons.contact_mail_outlined,
    'Outreach': Icons.forward_to_inbox,
    'Other': Icons.extension_outlined,
  };

  @override
  Widget build(BuildContext context) {
    final tools = ref.watch(toolsProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('All tools'),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(64),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
            child: SearchBar(
              hintText: 'Search tools',
              leading: const Icon(Icons.search),
              onChanged: (v) => setState(() => _q = v.toLowerCase()),
            ),
          ),
        ),
      ),
      body: AsyncBody<List<ToolSpec>>(
        value: tools,
        onRetry: () => ref.invalidate(toolsProvider),
        data: (list) {
          final filtered = list
              .where((t) => _q.isEmpty || t.name.contains(_q) || t.description.toLowerCase().contains(_q))
              .toList();
          final groups = <String, List<ToolSpec>>{};
          for (final t in filtered) {
            groups.putIfAbsent(t.category, () => []).add(t);
          }
          final cats = categoryOrder.where(groups.containsKey).toList();
          return RefreshIndicator(
            onRefresh: () => ref.refresh(toolsProvider.future),
            child: ListView(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                  child: Text('${list.length} tools available on your server',
                      style: Theme.of(context).textTheme.labelLarge),
                ),
                for (final c in cats) ...[
                  ListTile(
                    leading: Icon(_icons[c]),
                    title: Text(c, style: Theme.of(context).textTheme.titleMedium),
                    dense: true,
                  ),
                  for (final t in groups[c]!)
                    ListTile(
                      title: Text(t.title),
                      subtitle: Text(t.description, maxLines: 2, overflow: TextOverflow.ellipsis),
                      trailing: t.destructive
                          ? const Tooltip(message: 'Acts on your accounts', child: Icon(Icons.bolt))
                          : const Icon(Icons.chevron_right),
                      onTap: () => context.push('/tools/${t.name}'),
                    ),
                ],
              ],
            ),
          );
        },
      ),
    );
  }
}
