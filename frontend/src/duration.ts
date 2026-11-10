/** API timing fields remain milliseconds; display durations consistently in seconds. */
export const duration = (milliseconds: number): string => `${(milliseconds / 1000).toFixed(3)} seconds`;
