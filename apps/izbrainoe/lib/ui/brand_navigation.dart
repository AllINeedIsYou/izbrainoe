import 'dart:math';

import 'package:flutter/material.dart';

import 'widgets.dart';

/// The brand opens navigation without moving or rebuilding the chat underneath.
class BrandNavigation extends StatefulWidget {
  final int selected;
  final ValueChanged<int> onSelected;
  const BrandNavigation({
    super.key,
    required this.selected,
    required this.onSelected,
  });
  @override
  State<BrandNavigation> createState() => _BrandNavigationState();
}

class _BrandNavigationState extends State<BrandNavigation> {
  final anchor = GlobalKey();
  bool open = false;

  Future<void> toggle() async {
    if (open) return;
    FocusManager.instance.primaryFocus?.unfocus();
    final box = anchor.currentContext?.findRenderObject() as RenderBox?;
    if (box == null || !box.hasSize) return;
    final rect = box.localToGlobal(Offset.zero) & box.size;
    final screen = MediaQuery.of(context);
    final duration = screen.disableAnimations
        ? Duration.zero
        : const Duration(milliseconds: 210);
    setState(() => open = true);
    final selection = await showGeneralDialog<int>(
      context: context,
      barrierDismissible: true,
      barrierLabel: 'Закрыть меню разделов',
      barrierColor: Colors.black.withValues(alpha: .14),
      transitionDuration: duration,
      transitionBuilder: (_, _, _, child) => child,
      pageBuilder: (context, animation, _) {
        final eased = animation.drive(CurveTween(curve: Curves.easeOutCubic));
        final top = max(
          screen.padding.top + 4,
          min(
            rect.bottom + 8,
            screen.size.height -
                screen.padding.bottom -
                screen.viewInsets.bottom -
                236,
          ),
        );
        return Stack(
          children: [
            Positioned(
              left: max(12, min(rect.left + 2, screen.size.width - 78)),
              top: top,
              width: 64,
              child: FadeTransition(
                opacity: eased,
                child: SlideTransition(
                  position: Tween<Offset>(
                    begin: const Offset(0, -.055),
                    end: Offset.zero,
                  ).animate(eased),
                  child: ScaleTransition(
                    scale: Tween<double>(begin: .94, end: 1).animate(eased),
                    alignment: Alignment.topCenter,
                    child: _NavigationColumn(
                      selected: widget.selected,
                      onSelected: (value) => Navigator.pop(context, value),
                    ),
                  ),
                ),
              ),
            ),
          ],
        );
      },
    );
    if (!mounted) return;
    setState(() => open = false);
    if (selection != null) widget.onSelected(selection);
  }

  @override
  Widget build(BuildContext context) => Tooltip(
    message: 'Разделы',
    child: Semantics(
      button: true,
      expanded: open,
      child: InkWell(
        key: anchor,
        onTap: toggle,
        borderRadius: BorderRadius.circular(12),
        splashColor: accent.withValues(alpha: .07),
        highlightColor: accent.withValues(alpha: .04),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 5, horizontal: 3),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(10),
                child: Image.asset(
                  'assets/logo.png',
                  width: 34,
                  height: 34,
                  filterQuality: FilterQuality.high,
                ),
              ),
              const SizedBox(width: 10),
              const Flexible(
                child: Text(
                  'Изbrainое',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontWeight: FontWeight.w500, fontSize: 21),
                ),
              ),
              const SizedBox(width: 5),
              AnimatedRotation(
                turns: open ? .5 : 0,
                duration: const Duration(milliseconds: 190),
                curve: Curves.easeOutCubic,
                child: const Icon(
                  Icons.keyboard_arrow_down,
                  size: 16,
                  color: muted,
                ),
              ),
            ],
          ),
        ),
      ),
    ),
  );
}

class _NavigationColumn extends StatelessWidget {
  final int selected;
  final ValueChanged<int> onSelected;
  const _NavigationColumn({required this.selected, required this.onSelected});
  @override
  Widget build(BuildContext context) {
    const names = ['Чат', 'Поиск', 'Подборки', 'Пароли'];
    const icons = [
      Icons.chat_bubble_outline,
      Icons.search,
      Icons.folder_outlined,
      Icons.lock_outline,
    ];
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(21),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: .38),
            blurRadius: 22,
            offset: const Offset(0, 9),
          ),
        ],
      ),
      child: Material(
        color: const Color(0xff1c1c1c),
        clipBehavior: Clip.antiAlias,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(21),
          side: BorderSide(color: accent.withValues(alpha: .12)),
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              for (var i = 0; i < names.length; i++)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 2),
                  child: Semantics(
                    selected: selected == i,
                    child: IconButton(
                      tooltip: names[i],
                      onPressed: () => onSelected(i),
                      icon: Icon(icons[i], size: 23),
                      style: IconButton.styleFrom(
                        minimumSize: const Size(48, 48),
                        maximumSize: const Size(48, 48),
                        padding: EdgeInsets.zero,
                        backgroundColor: selected == i
                            ? accent
                            : Colors.transparent,
                        foregroundColor: selected == i ? background : muted,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(14),
                        ),
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
