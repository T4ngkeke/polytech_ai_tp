/** "1 lab", "3 labs"; many: the plural when it is not word + "s". */
export function plural(n, word, many = `${word}s`) {
  return `${n} ${n === 1 ? word : many}`;
}
