import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { useTheme } from "@/lib/theme";

interface Props {
  option: echarts.EChartsOption;
  height?: number;
}

/** ECharts 轻量封装：option 变化时整体 setOption，容器尺寸变化时自动 resize，跟随主题切换。 */
export function EChart({ option, height = 320 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const theme = useTheme();

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, theme === "dark" ? "dark" : undefined);
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, [theme]);

  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option, theme]);

  return <div ref={ref} style={{ height, width: "100%" }} />;
}
