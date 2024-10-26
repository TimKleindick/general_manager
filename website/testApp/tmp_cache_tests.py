import inspect
from functools import wraps

# direct dependency:
# {class_name.property_name: {id1, id2, ...}}

# filter dependency:
# {class_name: {filter_dict1, filter_dict2, ...}}
# -> filter/exclude => filter_dict = {"type": "filter/exclude", "query": {property_name: filter_value}, "id_set": {id1, id2, ...}}
# -> all => filter_dict = {"type": "filter", "query": {}, "id_set": {id1, id2, ...}}


class CacheTracker:
    def __init__(self):
        self.__direct_dependencies = dict()
        self.__filter_dependencies = dict()

    @property
    def _dependencies(self):
        return {
            "direct": self.__direct_dependencies.copy(),
            "filter": self.__filter_dependencies.copy(),
        }

    def add_direct_dependency(self, class_name, property_name, id):
        if class_name not in self.__direct_dependencies:
            self.__direct_dependencies[f"{class_name}.{property_name}"] = set()
        self.__direct_dependencies[f"{class_name}.{property_name}"].add(id)

    def add_filter_dependency(self, class_name, type, query, id_set):
        if class_name not in self.__filter_dependencies:
            self.__filter_dependencies[class_name] = set()
        self.__filter_dependencies[class_name].add(
            {"type": type, "query": query, "id_set": id_set}
        )


def custom_property(func):
    @wraps(func)
    def wrapper(self):
        # Hole Informationen über den Aufrufer
        stack = inspect.stack()
        own_class = self
        caller_frame = stack[1]  # Der Aufrufer ist im vorherigen Stack-Frame
        caller_class = caller_frame.frame.f_locals.get("self", None)
        caller_function = caller_frame.function

        # Herausfinden, wie das Property genannt wird
        property_name = func.__name__
        if caller_class and isinstance(caller_class, CacheTracker):
            caller_class.add_direct_dependency(
                own_class.__class__.__name__, property_name, own_class.id
            )

        # Führe die eigentliche Funktion aus
        return func(self)

    return property(wrapper)  # Hier wird der Wrapper in ein echtes Property verwandelt


# Beispielanwendung in einer Klasse
class MyClass:
    def __init__(self, id):
        self._value = id**3
        self.id = id

    @custom_property
    def value(self):
        return self._value


# Testen des Decorators
class test(CacheTracker):
    def __init__(self):
        super().__init__()
        self.obj = MyClass(42)

    def print(self):
        return self.obj.value


test_obj = test()
print(test_obj.print())  # Dies sollte den Wert korrekt zurückgeben
print(test_obj._dependencies)  # Dies sollte jetzt den Wert korrekt zurückgeben


# Möglichkeit die Dependencies für eine Funktion zu tracken
class NeutralClass(CacheTracker):
    _cache = {}

    @classmethod
    def cache_info(cls, key, value):
        cls._cache[key] = value

    @classmethod
    def get_cache(cls, key):
        return cls._cache.get(key, None)

    @classmethod
    def process_function(cls, func, *args, **kwargs):
        # Diese Methode verarbeitet die eigentliche Funktion
        cache_key = f"{func.__name__}_{args}_{kwargs}"
        cached_result = cls.get_cache(cache_key)
        if cached_result is not None:
            print("Ergebnis aus dem Cache.")
            return cached_result

        # Funktion ausführen und Ergebnis cachen
        result = custom_property(func(*args, **kwargs))
        cls.cache_info(cache_key, result)
        return result


def cache_result(func):
    def wrapper(*args, **kwargs):
        # Ruft die Funktion als Methode der NeutralClass auf
        return NeutralClass.process_function(func, *args, **kwargs)

    return wrapper


# Funktion außerhalb der Klasse
@cache_result
def my_function(x, y):
    print("Funktion wird ausgeführt.")
    return x + y


# Funktion normal aufrufen
a = my_function(2, 3)  # Funktion wird ausgeführt
print(a)

b = my_function(2, 3)  # Ergebnis aus dem Cache
print(b)


##### Meta Klasse für Post Init funktion


class PostInitMeta(type):
    def __call__(cls, *args, **kwargs):
        # Erzeuge die Instanz der Klasse
        obj = super().__call__(*args, **kwargs)

        # Rufe automatisch eine "post_init" Methode auf, wenn sie existiert
        if hasattr(obj, "post_init"):
            obj.post_init()

        return obj


class ElternKlasse(metaclass=PostInitMeta):
    def __init__(self):
        print("ElternKlasse __init__")

    def post_init(self):
        print("ElternKlasse post_init wird nach __init__ ausgeführt.")


class KindKlasse(ElternKlasse):
    def __init__(self):
        print("KindKlasse __init__")

    def post_init(self):
        print("KindKlasse post_init wird nach __init__ ausgeführt.")


# Beispiel
obj = KindKlasse()
